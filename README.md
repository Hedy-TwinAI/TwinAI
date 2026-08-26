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

The full model lives in [`BrewLine.py`](BrewLine.py); see its module
docstring for the exact scenario, KPI definitions, and conventions.

## Repo layout

```
BrewLine.py             SimPy model, KPI computation, CLI entry point
server/main.py          Flask wrapper exposing BrewLine as a JSON API
dashboard/               React + Vite frontend (charts + 3D scene)
  src/App.jsx              top-level layout and simulation-run orchestration
  src/components/          KPI cards, charts, playback controls, 3D scene
  src/lib/                 trace resampling and per-customer reconstruction
  src/hooks/               playback-clock hook
  public/models/           humanoid model asset (glTF) for the 3D scene
brewline_results.json    example output of a standalone `BrewLine.py` run
.claude/skills/simpy-scaffold/   a reusable Claude Code skill for scaffolding
                                 similar SimPy queueing models
```

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

This builds and starts both services: the Flask backend
(`http://localhost:8000`) and the dashboard, served by nginx and proxying
`/api` to the backend (`http://localhost:8080`). Open `http://localhost:8080`.

## Running the simulation standalone

```bash
uv run python BrewLine.py --reps 50 --out brewline_results.json
```

Key flags (all optional): `--arrival-rate`, `--num-baristas`,
`--mean-service-time`, `--horizon`, `--reps`, `--seed`, `--out`, `--verbose`.
Run `uv run python BrewLine.py --help` for the full list.

To validate a results file against the closed-form M/M/c solution:

```bash
uv run python .claude/skills/simpy-scaffold/scripts/validate_results.py brewline_results.json
```
