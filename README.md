# BrewLine

A digital twin of a coffee bar: a statistically-sound [SimPy](https://simpy.readthedocs.io/)
discrete-event simulation, served by a Flask backend and visualized in a live
React dashboard — including an animated 3D scene of customers arriving,
queueing, and being served.

## How it works

Customers arrive at a coffee bar according to a Poisson process and are served
by a pool of baristas (a single SimPy `Resource` with capacity `num_baristas`).
If a barista is free, service starts immediately; otherwise the customer waits
in a FIFO queue. Arrivals stop at `horizon` minutes, then the simulation
drains — every admitted customer is served to completion, so no wait is
truncated by the end of the run.

Each run executes many independent replications (default 50) and reports
KPIs with 95% confidence intervals: utilization, throughput, Cmax, average/
max/p95 wait, average/max queue length, average/max WIP, and average
sojourn time. Time-weighted quantities (queue length, WIP) are integrated
over time rather than sampled per-customer, and the model cross-checks
itself against Little's law and, for the plain case, the closed-form M/M/c
solution.

The full model lives in [`brewline/BrewLine.py`](brewline/BrewLine.py); see
its module docstring for the exact scenario, KPI definitions, and
conventions.

## Repo layout

```
brewline/                core Python package
  BrewLine.py               SimPy model, KPI computation, CLI entry point
  search_index.py           chunks/embeds/uploads results into Azure AI Search (RAG)
  sql_source.py             read-only Azure SQL access (Entra ID auth)
server/                  Flask backend
  main.py                   wraps BrewLine as a JSON API + /api/assistant
  Dockerfile
mcp_server.py            MCP server: read/summarize results, query Azure SQL (stdio or HTTP)
scripts/build_search_index.py   manual reindex of an existing results file
data/brewline_results.json   example output of a standalone BrewLine.py run
archive/simulation.py    superseded early draft, kept for reference
media/BrewLine_demo.mov  demo recording
dashboard/               React + Vite frontend (charts + 3D scene)
  src/App.jsx               top-level layout and simulation-run orchestration
  src/components/           KPI cards, charts, playback controls, 3D scene
  src/lib/                  trace resampling and per-customer reconstruction
  src/hooks/                playback-clock hook
  public/models/            humanoid model asset (glTF) for the 3D scene
.claude/skills/simpy-scaffold/   a reusable Claude Code skill for scaffolding
                                 similar SimPy queueing models
```

`pyproject.toml`/`uv.lock`, `.env`/`.env.example`, `docker-compose.yml`,
`.mcp.json`, and `.python-version` stay at the repo root — each is where its
own tool (uv, docker compose, Claude Code, python-dotenv) expects to find it.

## Setup

Requires [uv](https://docs.astral.sh/uv/) (Python) and Node.js 20+ (frontend).

```bash
# Python deps (SimPy + Flask backend), pinned via uv.lock
uv sync

# Frontend deps
cd dashboard && npm install
```

Copy `.env.example` to `.env` to configure the backend's default scenario
(arrival rate, baristas, service time, horizon, replications, seed) without
touching code — see [server/main.py](server/main.py) for how each
`BREWLINE_*` variable is used. Values can still be overridden per-request via
the dashboard's sliders or the `/api/simulate` request body; `.env` only sets
the defaults. `docker compose` picks up the same file automatically.

### AI assistant (`/api/assistant`)

A `POST /api/assistant` endpoint lets visitors ask gpt-4o questions about the
simulation. It grounds its answers two ways:

- **Live database access, via the MCP server** — when `MCP_SERVER_URL` is
  set, the endpoint connects to `mcp_server.py` as an MCP client and gives
  gpt-4o its tools (`query_sql`, `list_sql_tables`, `read_results`,
  `summarize_results`) as function-calling tools. For a question like "how
  many runs used 3 baristas?" or "what was the average wait time in the
  latest run?", the model issues its own read-only `SELECT`s against the
  historic runs in Azure SQL and answers from the real result — see
  [MCP server](#mcp-server) below for how to stand that server up.
- **RAG over the most recently generated results file** — retrieved chunks
  of `data/brewline_results.json` (config, per-KPI stats, per-barista
  utilization, the Little's-law check), plus optional live simulation
  context passed in the request body. Used whenever `MCP_SERVER_URL` isn't
  set, or as extra context when it is.

The endpoint is disabled (returns `503`) until the model itself is
configured:

```
AZURE_AI_FOUNDRY_ENDPOINT=          # https://<resource>.openai.azure.com/openai/v1
AZURE_AI_FOUNDRY_API_KEY=
AZURE_AI_FOUNDRY_MODEL=             # chat deployment name (e.g. gpt-4o)
```

Database tool-calling (optional — see [MCP server](#mcp-server)):

```
MCP_SERVER_URL=                     # e.g. http://mcp:8765/mcp under docker compose
MCP_AUTH_TOKEN=                     # must match the MCP server's own MCP_AUTH_TOKEN
```

RAG over the results file (optional):

```
VOYAGE_API_KEY=                     # https://dash.voyageai.com/api-keys
VOYAGE_MODEL=voyage-3               # embedding model
AZURE_SEARCH_ENDPOINT=
AZURE_SEARCH_API_KEY=
AZURE_SEARCH_INDEX_NAME=brewline-docs
```

Once `VOYAGE_API_KEY` is set, every `uv run python brewline/BrewLine.py ...`
run automatically reindexes `data/brewline_results.json` right after writing
it (see `brewline/search_index.py` and `brewline/BrewLine.py`'s `main()`) —
no separate step needed. To reindex an existing file by hand instead:

```bash
uv run python scripts/build_search_index.py [path/to/results.json]
```

Both groundings degrade gracefully: if the MCP server is unreachable, or
search/embeddings aren't configured, the assistant still answers using only
the question and any `context` passed in — neither is a precondition.

## Running the dashboard

Two processes, run from the repo root and from `dashboard/` respectively:

```bash
# Terminal 1 — backend API (http://127.0.0.1:8000)
uv run python server/main.py

# Terminal 2 — frontend dev server (http://localhost:5173, proxies /api to the backend)
cd dashboard && npm run dev
```

Open the printed `localhost` URL. Adjust arrival rate, barista count, mean
service time, and horizon with the sliders — each change re-runs the
simulation live. Use the playback controls to scrub through the day; the KPI
cards, charts, and 3D scene all stay in sync.

Other frontend commands (run from `dashboard/`): `npm run build` (production
bundle), `npm run lint` (oxlint), `npm run preview` (serve the production
build).

### Running with Docker

```bash
docker compose up --build
```

This builds and starts three services: the MCP server over streamable-http
(`http://localhost:8765/mcp`, see [MCP server](#mcp-server)), the Flask
backend (`http://localhost:8000`, wired to that MCP server automatically),
and the dashboard, served by nginx and proxying `/api` to the backend
(`http://localhost:8080`). Open `http://localhost:8080`. Set `MCP_AUTH_TOKEN`
in `.env` before exposing this beyond your own machine.

## Running the simulation standalone

```bash
uv run python brewline/BrewLine.py --reps 50 --out data/brewline_results.json
```

Key flags (all optional): `--arrival-rate`, `--num-baristas`,
`--mean-service-time`, `--horizon`, `--reps`, `--seed`, `--out`, `--verbose`.
Run `uv run python brewline/BrewLine.py --help` for the full list.

To validate a results file against the closed-form M/M/c solution:

```bash
uv run python .claude/skills/simpy-scaffold/scripts/validate_results.py data/brewline_results.json
```

## MCP server

`mcp_server.py` exposes the simulation over the Model Context Protocol. Two
transports:

- **stdio** (default) — for local MCP hosts (Claude Code, Claude Desktop)
  that spawn the server as a subprocess. Not reachable over the network.
- **streamable-http** — an actual network server, for `/api/assistant`
  above and for any remote MCP client (Claude.ai's "Connect apps",
  ChatGPT's connectors, etc.):

  ```bash
  MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8765 \
      MCP_AUTH_TOKEN=$(openssl rand -hex 32) uv run python mcp_server.py
  ```

  `MCP_AUTH_TOKEN` gates every request behind `Authorization: Bearer
  <token>` — set it whenever `MCP_HOST` is reachable from outside
  localhost, since `query_sql` otherwise lets anyone who can reach the port
  run arbitrary read-only `SELECT`s against the database. `docker compose
  up` runs this transport automatically as the `mcp` service, reachable at
  `localhost:8765` on the host and `http://mcp:8765/mcp` from the `backend`
  service.

Four tools:

- `read_results` — read `data/brewline_results.json` as JSON
- `summarize_results` — a concise, human-readable summary of the config and
  KPIs (same chunking as the search index)
- `list_sql_tables` / `query_sql` — read-only access to an Azure SQL
  Database (see below); `query_sql` only allows a single `SELECT` statement,
  no mutations or statement-stacking

Configure the SQL tools in `.env`:

```
AZURE_SQL_SERVER=<name>.database.windows.net
AZURE_SQL_DATABASE=<database>
```

Auth is Microsoft Entra ID only (`Authentication=ActiveDirectoryDefault` in
`sql_source.py` — no SQL login/password involved). Locally this resolves via
the logged-in `az` CLI session, so `az login` must be current; the same code
would resolve via managed identity if this server ran inside Azure. The
server's firewall also needs a rule allowing your client IP:

```bash
az sql server firewall-rule create --server <name> --resource-group <rg> \
  --name allow-my-ip --start-ip-address <ip> --end-ip-address <ip>
```

### Deploying to Azure Container Apps

`scripts/deploy_mcp.sh` deploys the streamable-http transport as a third
Container App (`mcp`) alongside the existing `backend`/`dashboard` apps in
`brewline-rg`, sharing their environment (`brewline-env`) and registry
(`brewlineregistry`):

```bash
./scripts/deploy_mcp.sh
```

It builds the image via `az acr build` (no local Docker needed), creates the
app with a system-assigned managed identity for both ACR pull and
`AZURE_SQL_SERVER`/`AZURE_SQL_DATABASE` access (Entra ID auth resolves via
that identity in Azure the same way it resolves via `az login` locally —
see above), and generates an `MCP_AUTH_TOKEN`. It then prints the one-time
SQL grant for the new identity and the command to wire `backend`'s
`MCP_SERVER_URL`/`MCP_AUTH_TOKEN` at it, since those touch the already-live
`backend` app and are left as an explicit step rather than done silently.

Example host config (Claude Desktop's `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "brewline": {
      "command": "uv",
      "args": ["run", "python", "mcp_server.py"],
      "cwd": "/absolute/path/to/TwinAI"
    }
  }
}
```
