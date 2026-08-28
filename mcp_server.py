"""MCP server exposing the BrewLine coffee-shop digital twin.

Tools:
    read_results       read data/brewline_results.json
    edit_inputs        re-run the simulation with new inputs, overwriting
                        data/brewline_results.json (and reindexing it for
                        RAG, same as brewline/BrewLine.py's CLI)
    summarize_results   a concise, human-readable summary of the results
    list_sql_tables     list tables in the Azure SQL database
    query_sql           run a read-only SELECT against the Azure SQL database

Run:
    uv run python mcp_server.py
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from brewline import sql_source
from brewline.BrewLine import _round, run_experiment
from brewline.search_index import CONFIGURED, chunk_results, index_results

REPO_ROOT = Path(__file__).resolve().parent
RESULTS_PATH = REPO_ROOT / "data" / "brewline_results.json"

DEFAULT_INPUTS = {
    "arrival_rate": 0.5,
    "num_baristas": 2,
    "mean_service_time": 3.0,
    "horizon": 480.0,
    "reps": 50,
    "seed": 42,
}

mcp = FastMCP("BrewLine", log_level="ERROR")


def _load_results(path: str) -> dict:
    file = Path(path)
    if not file.exists():
        raise ValueError(f"No results file at {file} — run edit_inputs first to generate one")
    return json.loads(file.read_text())


def _current_inputs() -> dict:
    if not RESULTS_PATH.exists():
        return dict(DEFAULT_INPUTS)
    cfg = json.loads(RESULTS_PATH.read_text())["config"]
    return {
        "arrival_rate": cfg["arrival_rate_per_min"],
        "num_baristas": cfg["num_baristas"],
        "mean_service_time": cfg["mean_service_time_min"],
        "horizon": cfg["horizon_min"],
        "reps": cfg["replications"],
        "seed": cfg["master_seed"],
    }


@mcp.tool(
    name="read_results",
    description=(
        "Read data/brewline_results.json (config, KPI summary, per-barista "
        "utilization, per-replication data, and the Little's-law validation "
        "check) and return it as JSON."
    ),
)
def read_results(
    path: str = Field(default=str(RESULTS_PATH), description="Path to the results file"),
) -> dict:
    return _load_results(path)


@mcp.tool(
    name="edit_inputs",
    description=(
        "Re-run the BrewLine simulation with new inputs, overwriting "
        "data/brewline_results.json. Any input left unset keeps its current "
        "value from the existing results file (or BrewLine's default if no "
        "results file exists yet)."
    ),
)
def edit_inputs(
    arrival_rate: float | None = Field(default=None, gt=0, description="Customers per minute"),
    num_baristas: int | None = Field(default=None, ge=1, description="Number of baristas"),
    mean_service_time: float | None = Field(
        default=None, gt=0, description="Average minutes per customer"
    ),
    horizon: float | None = Field(
        default=None, gt=0, description="Minutes during which customers arrive"
    ),
    reps: int | None = Field(default=None, ge=1, description="Number of independent replications"),
    seed: int | None = Field(default=None, description="Master random seed"),
) -> dict:
    current = _current_inputs()
    inputs = {
        "arrival_rate": arrival_rate if arrival_rate is not None else current["arrival_rate"],
        "num_baristas": num_baristas if num_baristas is not None else current["num_baristas"],
        "mean_service_time": (
            mean_service_time if mean_service_time is not None else current["mean_service_time"]
        ),
        "horizon": horizon if horizon is not None else current["horizon"],
        "reps": reps if reps is not None else current["reps"],
        "seed": seed if seed is not None else current["seed"],
    }

    results = run_experiment(**inputs)
    rounded = _round(results)
    RESULTS_PATH.write_text(json.dumps(rounded, indent=2))

    indexed_chunks = index_results(rounded) if CONFIGURED else 0

    return {"config": rounded["config"], "indexed_chunks": indexed_chunks}


@mcp.tool(
    name="summarize_results",
    description="Return a concise, human-readable summary of data/brewline_results.json's config and KPIs.",
)
def summarize_results(
    path: str = Field(default=str(RESULTS_PATH), description="Path to the results file"),
) -> str:
    results = _load_results(path)
    chunks = chunk_results(results)
    return "\n".join(f"{title}: {content}" for title, content in chunks)


@mcp.tool(
    name="list_sql_tables",
    description="List the tables (schema + name) in the Azure SQL database.",
)
def list_sql_tables() -> list[dict]:
    return sql_source.list_tables()


@mcp.tool(
    name="query_sql",
    description=(
        "Run a read-only SELECT query against the Azure SQL database and "
        "return its columns and rows. Only a single SELECT statement is "
        "allowed (no mutations, no statement stacking)."
    ),
)
def query_sql(
    sql: str = Field(description="A single SELECT (or WITH ... SELECT) statement"),
    max_rows: int = Field(default=200, ge=1, le=1000, description="Maximum rows to return"),
) -> dict:
    return sql_source.run_query(sql, max_rows=max_rows)


if __name__ == "__main__":
    mcp.run(transport="stdio")
