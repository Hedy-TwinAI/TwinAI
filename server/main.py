"""Flask backend for the BrewLine dashboard.

Wraps `BrewLine.run_experiment` so the dashboard's input knobs (arrival
rate, #servers, mean service time) can trigger a live re-run of the real
SimPy model instead of reading a static, pre-computed JSON file.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import AzureError
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from openai import APIError, OpenAI
from pydantic import BaseModel, Field, ValidationError

from brewline.BrewLine import _round, run_experiment
from brewline.search_index import VOYAGE_API_KEY, embed_query

load_dotenv()

app = Flask(__name__)

DEFAULT_ARRIVAL_RATE = float(os.environ.get("BREWLINE_ARRIVAL_RATE", 0.5))
DEFAULT_NUM_BARISTAS = int(os.environ.get("BREWLINE_NUM_BARISTAS", 2))
DEFAULT_MEAN_SERVICE_TIME = float(os.environ.get("BREWLINE_MEAN_SERVICE_TIME", 3.0))
DEFAULT_HORIZON = float(os.environ.get("BREWLINE_HORIZON", 480.0))
DEFAULT_REPS = int(os.environ.get("BREWLINE_REPS", 50))
DEFAULT_SEED = int(os.environ.get("BREWLINE_SEED", 42))

AZURE_AI_FOUNDRY_ENDPOINT = os.environ.get("AZURE_AI_FOUNDRY_ENDPOINT", "")
AZURE_AI_FOUNDRY_API_KEY = os.environ.get("AZURE_AI_FOUNDRY_API_KEY", "")
AZURE_AI_FOUNDRY_MODEL = os.environ.get("AZURE_AI_FOUNDRY_MODEL", "")

AZURE_SEARCH_ENDPOINT = os.environ.get("AZURE_SEARCH_ENDPOINT", "")
AZURE_SEARCH_API_KEY = os.environ.get("AZURE_SEARCH_API_KEY", "")
AZURE_SEARCH_INDEX_NAME = os.environ.get("AZURE_SEARCH_INDEX_NAME", "brewline-docs")

ASSISTANT_SYSTEM_PROMPT = (
    "You are an assistant embedded in the BrewLine dashboard, a SimPy digital "
    "twin of a coffee bar. Customers arrive via a Poisson process and are "
    "served by a pool of baristas (an M/M/c queue). KPIs include barista "
    "utilization, throughput, Cmax, average/max/p95 wait, average/max queue "
    "length, average/max WIP, and sojourn time, each with a 95% confidence "
    "interval across replications. You may be given retrieved data from the "
    "most recently indexed BrewLine run and/or live simulation context "
    "(JSON) alongside the question — answer concisely, grounded in whatever "
    "is provided, and reference specific numbers when given; say so plainly "
    "if neither was provided."
)

_assistant_client: OpenAI | None = None
_search_client: SearchClient | None = None


def _get_assistant_client() -> OpenAI | None:
    global _assistant_client
    if not (AZURE_AI_FOUNDRY_ENDPOINT and AZURE_AI_FOUNDRY_API_KEY and AZURE_AI_FOUNDRY_MODEL):
        return None
    if _assistant_client is None:
        _assistant_client = OpenAI(
            base_url=AZURE_AI_FOUNDRY_ENDPOINT,
            api_key=AZURE_AI_FOUNDRY_API_KEY,
        )
    return _assistant_client


def _get_search_client() -> SearchClient | None:
    global _search_client
    if not (AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_API_KEY and AZURE_SEARCH_INDEX_NAME):
        return None
    if _search_client is None:
        _search_client = SearchClient(
            endpoint=AZURE_SEARCH_ENDPOINT,
            index_name=AZURE_SEARCH_INDEX_NAME,
            credential=AzureKeyCredential(AZURE_SEARCH_API_KEY),
        )
    return _search_client


def _retrieve_context(question: str, top_k: int = 5) -> list[str]:
    """Vector-search the doc index for snippets relevant to `question`.

    Returns an empty list (rather than raising) if RAG isn't configured or
    the search call fails — retrieval is a best-effort enhancement, not a
    precondition for the assistant to answer.
    """
    search_client = _get_search_client()
    if not VOYAGE_API_KEY or search_client is None:
        return []

    try:
        embedding = embed_query(question)
    except Exception:
        return []

    try:
        results = search_client.search(
            search_text=None,
            vector_queries=[
                VectorizedQuery(vector=embedding, k_nearest_neighbors=top_k, fields="contentVector"),
            ],
            select=["content"],
        )
        return [doc["content"] for doc in results]
    except AzureError:
        return []


class AssistantRequest(BaseModel):
    question: str
    context: dict | None = None


class SimulateRequest(BaseModel):
    arrival_rate: float = Field(DEFAULT_ARRIVAL_RATE, gt=0)
    num_baristas: int = Field(DEFAULT_NUM_BARISTAS, ge=1)
    mean_service_time: float = Field(DEFAULT_MEAN_SERVICE_TIME, gt=0)
    horizon: float = Field(DEFAULT_HORIZON, gt=0)
    reps: int = Field(DEFAULT_REPS, ge=1)
    seed: int = DEFAULT_SEED


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/simulate")
def simulate():
    try:
        req = SimulateRequest.model_validate(request.get_json(force=True, silent=True) or {})
    except ValidationError as exc:
        return jsonify({"detail": exc.errors()}), 422

    try:
        result = run_experiment(
            arrival_rate=req.arrival_rate,
            num_baristas=req.num_baristas,
            mean_service_time=req.mean_service_time,
            horizon=req.horizon,
            reps=req.reps,
            seed=req.seed,
        )
    except ValueError as exc:
        return jsonify({"detail": str(exc)}), 400

    return jsonify(_round(result))


@app.post("/api/assistant")
def assistant():
    client = _get_assistant_client()
    if client is None:
        return jsonify({"detail": "Assistant not configured"}), 503

    try:
        req = AssistantRequest.model_validate(request.get_json(force=True, silent=True) or {})
    except ValidationError as exc:
        return jsonify({"detail": exc.errors()}), 422

    user_content = req.question

    retrieved = _retrieve_context(req.question)
    if retrieved:
        user_content += "\n\nRetrieved data from the indexed run:\n" + "\n---\n".join(retrieved)

    if req.context is not None:
        user_content += "\n\nSimulation context (JSON):\n" + json.dumps(req.context)

    try:
        response = client.chat.completions.create(
            model=AZURE_AI_FOUNDRY_MODEL,
            messages=[
                {"role": "system", "content": ASSISTANT_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
    except APIError as exc:
        return jsonify({"detail": str(exc)}), 502

    return jsonify({"answer": response.choices[0].message.content})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
