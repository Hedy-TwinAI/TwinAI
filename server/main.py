"""Flask backend for the BrewLine dashboard.

Wraps `BrewLine.run_experiment` so the dashboard's input knobs (arrival
rate, #servers, mean service time) can trigger a live re-run of the real
SimPy model instead of reading a static, pre-computed JSON file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from pydantic import BaseModel, Field, ValidationError

from BrewLine import _round, run_experiment

load_dotenv()

app = Flask(__name__)

DEFAULT_ARRIVAL_RATE = float(os.environ.get("BREWLINE_ARRIVAL_RATE", 0.5))
DEFAULT_NUM_BARISTAS = int(os.environ.get("BREWLINE_NUM_BARISTAS", 2))
DEFAULT_MEAN_SERVICE_TIME = float(os.environ.get("BREWLINE_MEAN_SERVICE_TIME", 3.0))
DEFAULT_HORIZON = float(os.environ.get("BREWLINE_HORIZON", 480.0))
DEFAULT_REPS = int(os.environ.get("BREWLINE_REPS", 50))
DEFAULT_SEED = int(os.environ.get("BREWLINE_SEED", 42))


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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
