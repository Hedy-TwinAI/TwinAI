"""Flask backend for the BrewLine dashboard.

Wraps `BrewLine.run_experiment` so the dashboard's input knobs (arrival
rate, #servers, mean service time) can trigger a live re-run of the real
SimPy model instead of reading a static, pre-computed JSON file.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import AzureError
from azure.monitor.opentelemetry import configure_azure_monitor
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from openai import APIError, OpenAI
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from pydantic import BaseModel, Field, ValidationError

from brewline.BrewLine import _round, run_experiment
from brewline.search_index import VOYAGE_API_KEY, embed_query

load_dotenv()

# Leave unset locally to skip telemetry; set in Container Apps to send
# request/dependency/exception telemetry to Application Insights.
if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    configure_azure_monitor()

app = Flask(__name__)

if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    # configure_azure_monitor()'s automatic instrumentation patches Flask at
    # the class level (Flask.wsgi_app) via an entry-point scan, which is a
    # silent no-op against this Flask/Python combo -- no error, no request
    # telemetry either. instrument_app() patches this instance directly and
    # actually works, so call it explicitly instead of relying on the
    # automatic path.
    FlaskInstrumentor().instrument_app(app)

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

# The BrewLine MCP server (mcp_server.py run with MCP_TRANSPORT=streamable-http),
# giving gpt-4o tool-calling access to historic runs in the database on a
# visitor's behalf. Leave MCP_SERVER_URL blank to disable (falls back to
# answering from RAG/live context only, as before).
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "")
MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")
MCP_MAX_TOOL_ROUNDS = 5

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
    "if neither was provided. When tools are available, use query_sql to "
    "look up historic runs stored in the database rather than guessing, and "
    "read_results/summarize_results for the full detail of the most "
    "recently generated run.\n\n"
    "Database schema for query_sql (call list_sql_tables if you need to "
    "double-check what actually exists): one row per historic run in "
    "Inputs (InputID int primary key, arrival_rate_per_min, "
    "mean_interarrival_min, num_baristas, mean_service_time_min, "
    "horizon_min, replications, master_seed, offered_load_rho, stable bit, "
    "CreatedAt). Each KPI has its own table named kpi_<kpi_name> (e.g. "
    "kpi_utilization, kpi_throughput, kpi_avg_wait, kpi_max_wait, "
    "kpi_p95_wait, kpi_avg_queue_length, kpi_max_queue_length, kpi_avg_wip, "
    "kpi_max_wip, kpi_cmax, kpi_customers_served, kpi_avg_sojourn), each "
    "with columns (input_id int, referencing Inputs.InputID; mean, stdev, "
    "ci95_low, ci95_high, min_value, max_value — all float). "
    "run_barista_utilization has one row per (input_id, barista_index) with "
    "the same mean/stdev/ci95_low/ci95_high/min_value/max_value columns. "
    "run_validation has one row per input_id with "
    "littles_law_max_abs_error. Join on input_id/InputID; 'the most recent "
    "run' means the Inputs row with the highest InputID (or latest "
    "CreatedAt)."
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


def _mcp_tool_to_openai_schema(tool) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema,
        },
    }


def _mcp_tool_result_to_text(result) -> str:
    text = "\n".join(getattr(block, "text", None) or str(block) for block in result.content)
    return f"Tool error: {text}" if result.isError else text


async def _ask_with_mcp_tools(client: OpenAI, user_content: str) -> str | None:
    """Answer via gpt-4o with the BrewLine MCP server's tools (query_sql,
    list_sql_tables, read_results, summarize_results) available for
    tool-calling, so the model can look up historic runs in the database on
    the visitor's behalf instead of only answering from static RAG context.

    Returns None if MCP_SERVER_URL isn't configured, so the caller can fall
    back to answering without live database access.
    """
    if not MCP_SERVER_URL:
        return None

    headers = {"Authorization": f"Bearer {MCP_AUTH_TOKEN}"} if MCP_AUTH_TOKEN else None
    http_client = create_mcp_http_client(headers=headers)

    async with streamable_http_client(MCP_SERVER_URL, http_client=http_client) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [_mcp_tool_to_openai_schema(t) for t in (await session.list_tools()).tools]

            messages: list[dict] = [
                {"role": "system", "content": ASSISTANT_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ]

            for _ in range(MCP_MAX_TOOL_ROUNDS):
                response = client.chat.completions.create(
                    model=AZURE_AI_FOUNDRY_MODEL, messages=messages, tools=tools,
                )
                message = response.choices[0].message
                if not message.tool_calls:
                    return message.content

                messages.append({
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in message.tool_calls
                    ],
                })

                for tc in message.tool_calls:
                    try:
                        arguments = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    result = await session.call_tool(tc.function.name, arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": _mcp_tool_result_to_text(result),
                    })

            # Ran out of tool-call rounds -- ask once more without tools to force a final answer.
            response = client.chat.completions.create(model=AZURE_AI_FOUNDRY_MODEL, messages=messages)
            return response.choices[0].message.content


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

    if req.context is not None:
        # Live context is the caller's own current run -- it takes priority
        # over the vector index, which only holds whatever run was last
        # indexed (possibly stale, possibly someone else's). Retrieval is
        # only useful when there's no live run to answer from.
        user_content += "\n\nSimulation context (JSON):\n" + json.dumps(req.context)
    else:
        retrieved = _retrieve_context(req.question)
        if retrieved:
            user_content += "\n\nRetrieved data from the indexed run:\n" + "\n---\n".join(retrieved)

    answer = None
    if MCP_SERVER_URL:
        try:
            answer = asyncio.run(_ask_with_mcp_tools(client, user_content))
        except Exception:
            # The MCP server being unreachable/misbehaving shouldn't take the
            # assistant down -- fall back to answering without live DB access.
            app.logger.exception("MCP tool-calling path failed; falling back to a plain answer")

    if answer is None:
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
        answer = response.choices[0].message.content

    return jsonify({"answer": answer})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
