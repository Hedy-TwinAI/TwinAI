---
name: simpy-scaffold
description: Scaffold a complete, statistically sound SimPy discrete-event simulation of a queueing system — arrivals, queue, resources, service, multiple replications, KPIs (utilization, throughput, Cmax, wait, queue length, WIP), and a JSON export for a dashboard. Use this skill whenever the user mentions SimPy, discrete-event simulation, a digital twin, queueing models, M/M/1 or M/M/c, or wants to simulate any system where things arrive, wait in line, and get served — call centers, clinics, coffee shops, checkout lanes, factory lines, ticket queues, server request handling, elevator banks. Also use it when the user already has a simulation and asks for KPIs, replications, confidence intervals, warm-up handling, or model validation, even if they never say the word "SimPy".
---

# SimPy Model Scaffolder

Build a discrete-event simulation that a reasonable person could defend in a review. The mechanics of SimPy are easy; the reason simulation studies get thrown out is almost always statistics — one replication reported as fact, occupancy averaged the wrong way, or a units slip that silently scales the arrival rate.

This skill exists to get those parts right by construction.

## Workflow

1. **Elicit the system** (below). Do not skip to code — an unstated unit or an unasked question about balking gets baked into the model and is expensive to unwind later.
2. **Sanity-check the load** before writing anything. Compute ρ = λ·E[S]/c. If ρ ≥ 1 the queue is unstable and every wait statistic you produce will be meaningless — say so and get the parameters fixed first.
3. **Copy `assets/template.py`** into the project and rename it for the domain. It is runnable as-is, so you can smoke-test immediately and only then start editing.
4. **Adapt the model** to the elicited system. Read `references/patterns.md` if the system needs anything beyond one queue feeding one resource pool.
5. **Wire up the KPIs.** Read `references/kpis.md` — it has the correct formula for each one and the traps.
6. **Validate.** Run `scripts/validate_results.py` on the exported JSON. For a plain M/M/c system it also compares your output against the closed-form answer, which is the single most convincing evidence the model is right.
7. **Report** the KPI table with confidence intervals, and state the validation outcome.

## Eliciting the system

Get these before coding. Where the user doesn't know, propose a default and say what you assumed rather than stalling.

- **Entity** — what flows through? (customer, patient, order, request)
- **Resource(s)** — what serves it, and how many of each? Capacity is the single most important knob.
- **Arrival process** — rate or mean gap, and *which one* (see units, below). Constant, or time-varying by hour?
- **Service time** — mean, and distribution. Exponential is the default and gives closed-form validation; lognormal is usually more realistic for human work.
- **Queue discipline** — FIFO unless told otherwise. Priority? Do people give up?
- **Run policy** — does the system open and close (terminating), or run indefinitely (steady-state)? This changes the whole statistical treatment; see below.
- **Horizon and replications** — how long, and how many. Default 30–50 replications.

## The four things that go wrong

These are the reason this skill is more than "write some SimPy."

### 1. Rate vs. mean interarrival time

`expovariate` takes a **rate**, not a mean. `random.expovariate(arrival_rate)` and `random.expovariate(1/mean_gap)` are the same thing; `random.expovariate(mean_gap)` is a silent bug that scales your load by mean_gap². A model with `arrival_rate = 7` where 7 was meant to be "one every 7 minutes" runs 49× hot and nothing crashes.

Pick one convention, name the variable for it (`arrival_rate_per_min`, not `t_inter`), and put the unit in the name. Export both to the results JSON so a reader can spot the slip.

### 2. Time-weighted vs. per-entity averages

Queue length and WIP are **levels** — they exist at every instant, not once per entity. Their averages must be integrated over time:

    avg_queue_length = ∫ queue_length dt / T

Collecting `len(queue)` each time someone arrives and taking the mean of that list is wrong, and wrong in a specific direction: arrival-triggered sampling over-weights busy periods. Wait time and sojourn time are the opposite — those are per-entity, so you average the list.

The template does this with an `_advance()` accumulator called at every change point, *before* the counters mutate, so each interval is credited to the occupancy that actually held during it. Keep that discipline when you add new event types.

### 3. One replication is an anecdote

A single run gives you one sample from a random process. Report a mean over independent replications with a confidence interval, or you have not measured anything — run-to-run spread in these models is routinely large enough to reverse a conclusion.

Each replication needs its **own RNG stream**: `self.rng = random.Random(seed)`, never module-level `random.seed()`. Draw the per-replication seeds from one master `random.Random(master_seed)` so the whole experiment reproduces from a single number.

### 4. Terminating vs. steady-state

**Terminating** (a shop that opens and closes, a batch of jobs): stop *arrivals* at the horizon, then let the simulation drain so every admitted entity finishes. No warm-up is dropped — starting empty is part of the thing you're measuring. Cmax is the moment the last entity leaves. The template does this.

**Steady-state** (a system that runs continuously): the empty start biases everything downward, so discard a warm-up period before collecting statistics. Either run long enough that the transient is negligible and say so, or implement the discard explicitly. Never silently mix the two — a `env.run(until=T)` hard cutoff on a terminating model truncates the waits of everyone still in the queue and quietly deflates your wait KPIs.

## KPIs to produce

Always compute all of these; they're cheap once the accumulators exist, and reviewers ask for them. Formulas and pitfalls in `references/kpis.md`.

| KPI | Kind |
|---|---|
| Utilization (per resource pool) | ratio, needs an explicit denominator |
| Throughput | count / time |
| Cmax (makespan) | max departure time |
| Avg / max / p95 wait | per-entity |
| Avg / max queue length | **time-weighted** |
| Avg / max WIP | **time-weighted** |
| Avg sojourn time | per-entity |

## Output format

Export one JSON file with this shape. A dashboard needs both the aggregate and the per-replication rows — the aggregate to display, the rows to draw distributions.

```json
{
  "model": "<name>",
  "generated_at": "<ISO 8601 UTC>",
  "config": {
    "<param>": "<value>",
    "offered_load_rho": 0.75,
    "stable": true
  },
  "summary": {
    "<kpi>": {"mean": 0, "stdev": 0, "ci95_half_width": 0,
              "ci95_low": 0, "ci95_high": 0, "min": 0, "max": 0}
  },
  "replications": [{"replication": 0, "seed": 123, "<kpi>": 0}],
  "validation": {"littles_law_max_abs_error": 0.0}
}
```

Round floats before writing so the file stays readable. Keep `config` flat and self-describing with units in the key names — it is the only record of what was actually run.

## Validation

Two independent checks. Run both; they catch different bugs.

**Little's law** — L = λ_eff · W, computed inside the model. This cross-checks the time-weighted accumulators against the per-entity records. Because both sides are derived from the same run, agreement should be near machine precision (~1e-15). An error of 0.01 is not "close" here; it means an accumulator is missing a change point.

**Closed-form comparison** — for M/M/1 or M/M/c, the true steady-state answer is known. `scripts/validate_results.py` computes it from your `config` and checks whether it falls inside your confidence intervals:

```bash
python .claude/skills/simpy-scaffold/scripts/validate_results.py results.json
```

Expect terminating-simulation means to sit slightly *below* the steady-state values — the system starts empty and the transient drags the average down. That direction is correct and worth explaining in your report rather than hiding. A mean *above* the analytic value, or outside the CI, is a real signal.

Once the model has anything the closed form doesn't cover (lognormal service, balking, priorities), validate the simplified version first, then add the feature — that way a validation failure points at the feature and not at the whole model.

## Reference files

- `references/kpis.md` — every KPI's exact formula, the denominator question for utilization, and how to accumulate correctly
- `references/patterns.md` — SimPy recipes past the basic queue: priority, balking and reneging, time-varying arrivals, multi-stage flows, breakdowns, batching
- `references/validation.md` — closed-form queueing formulas (M/M/1, M/M/c, M/M/c/K, M/G/1) for hand-checking
- `assets/template.py` — the runnable starting point
- `scripts/validate_results.py` — schema, statistics, and analytic checks on an exported JSON
