"""MCP server exposing the BrewLine coffee-shop digital twin.

Tools:
    list_sql_tables     list tables in the Azure SQL database
    query_sql           run a read-only SELECT against the Azure SQL database

Run (stdio, for local MCP clients like Claude Code -- see .mcp.json):
    uv run python mcp_server.py

Run (HTTP, externally reachable -- e.g. for server/main.py's assistant
endpoint, or any remote MCP client):
    MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8765 \
        MCP_AUTH_TOKEN=<shared-secret> uv run python mcp_server.py

MCP_AUTH_TOKEN gates the HTTP transport with a bearer token (checked against
the `Authorization: Bearer <token>` header) -- set it whenever MCP_HOST is
reachable from outside localhost, since query_sql otherwise lets anyone who
can reach the port run arbitrary read-only SELECTs against the database.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from azure.monitor.opentelemetry import configure_azure_monitor
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from starlette.responses import JSONResponse

from brewline import sql_source
from brewline.BrewLine import _round, run_experiment
from brewline.search_index import CONFIGURED, chunk_results, index_results

# Leave unset locally to skip telemetry; set in Container Apps to send
# logs/dependency telemetry to Application Insights.
if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    configure_azure_monitor()

REPO_ROOT = Path(__file__).resolve().parent
RESULTS_PATH = REPO_ROOT / "data" / "brewline_results.json"

MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MCP_PORT", "8765"))
MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")

DEFAULT_INPUTS = {
    "arrival_rate": 0.5,
    "num_baristas": 2,
    "mean_service_time": 3.0,
    "horizon": 480.0,
    "reps": 50,
    "seed": 42,
}

mcp = FastMCP("BrewLine", log_level="ERROR", host=MCP_HOST, port=MCP_PORT)


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


class _BearerAuthMiddleware:
    """Minimal ASGI middleware requiring `Authorization: Bearer <token>`.

    FastMCP's own auth support assumes a full OAuth resource-server setup
    (issuer URL, protected-resource metadata, ...), which is overkill for
    gating a single-tenant HTTP deployment with one shared secret -- so this
    wraps the streamable-HTTP app directly instead.
    """

    def __init__(self, app, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        presented = headers.get(b"authorization", b"").decode("latin-1")
        if presented != f"Bearer {self._token}":
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        import uvicorn

        app = mcp.streamable_http_app()
        if MCP_AUTH_TOKEN:
            app = _BearerAuthMiddleware(app, MCP_AUTH_TOKEN)
        elif MCP_HOST not in ("127.0.0.1", "localhost", "::1"):
            print(
                f"WARNING: MCP_HOST={MCP_HOST!r} but MCP_AUTH_TOKEN is unset -- "
                "this MCP server (including query_sql) is reachable, unauthenticated, "
                "by anyone who can reach this host/port.",
                file=sys.stderr,
            )
        uvicorn.run(app, host=MCP_HOST, port=MCP_PORT, log_level="info")
