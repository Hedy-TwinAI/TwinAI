"""FastAPI backend for the BrewLine dashboard.

Wraps `BrewLine.run_experiment` so the dashboard's input knobs (arrival
rate, #servers, mean service time) can trigger a live re-run of the real
SimPy model instead of reading a static, pre-computed JSON file.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from BrewLine import _round, run_experiment

app = FastAPI(title="BrewLine dashboard API")


class SimulateRequest(BaseModel):
    arrival_rate: float = Field(0.5, gt=0)
    num_baristas: int = Field(2, ge=1)
    mean_service_time: float = Field(3.0, gt=0)
    horizon: float = Field(480.0, gt=0)
    reps: int = Field(50, ge=1)
    seed: int = 42


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/simulate")
def simulate(req: SimulateRequest) -> dict:
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _round(result)
