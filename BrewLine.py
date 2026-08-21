"""
BrewLine digital twin.

Scenario:
Customers arrive at a coffee bar and are served by a pool of baristas
(a single SimPy Resource with capacity = num_baristas).

Customers arrive according to a Poisson process (exponential interarrival
times) and are served with an exponential service time distribution. If a
barista is free the customer is served immediately; otherwise the customer
waits in a FIFO queue.

Run policy (terminating simulation):
    Arrivals stop at `horizon` (the shop stops letting people in), then the
    simulation drains -- every admitted customer is served to completion.
    Cmax is therefore the moment the last customer leaves, and no customer's
    wait is truncated by the end of the run. Because the run terminates, no
    warm-up period is dropped: replication means estimate the full-day mean.

Knobs (inputs):
    arrival_rate        customers per minute (lambda)
    num_baristas        number of baristas (servers)
    mean_service_time   average minutes per customer (1/mu)
    horizon             minutes during which arrivals occur

KPIs (outputs), per replication and aggregated across replications:
    utilization, throughput, Cmax, avg/max/p95 wait, avg/max queue length,
    avg/max WIP, avg sojourn time

Time-weighted quantities (queue length, WIP) are integrated over time, not
averaged over customers. Utilization and throughput use Cmax as the
denominator; `horizon` is also exported so a dashboard can renormalise.

Usage:
    python BrewLine.py --reps 50 --out brewline_results.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone

import simpy


@dataclass
class CustomerRecord:
    """One customer's journey through the shop."""

    id: int
    arrival_time: float
    start_time: float
    end_time: float
    service_time: float

    @property
    def wait_time(self) -> float:
        """Minutes spent queueing before a barista picked them up."""
        return self.start_time - self.arrival_time

    @property
    def sojourn_time(self) -> float:
        """Total minutes in the system (queue + service)."""
        return self.end_time - self.arrival_time


class BrewLine:
    """A single replication of the coffee bar."""

    def __init__(
        self,
        arrival_rate: float,
        num_baristas: int,
        mean_service_time: float,
        horizon: float,
        seed: int,
        verbose: bool = False,
        record_trace: bool = False,
    ):
        if arrival_rate <= 0:
            raise ValueError("arrival_rate must be > 0")
        if num_baristas < 1:
            raise ValueError("num_baristas must be >= 1")
        if mean_service_time <= 0:
            raise ValueError("mean_service_time must be > 0")
        if horizon <= 0:
            raise ValueError("horizon must be > 0")

        self.arrival_rate = arrival_rate
        self.num_baristas = num_baristas
        self.mean_service_time = mean_service_time
        self.horizon = horizon
        self.seed = seed
        self.verbose = verbose
        self.record_trace = record_trace

        # Each replication owns its RNG stream, so runs are independent of
        # each other and of module-level `random` state -- and reproducible.
        self.rng = random.Random(seed)

        self.env = simpy.Environment()
        self.baristas = simpy.Resource(self.env, capacity=num_baristas)
        self.records: list[CustomerRecord] = []

        # Accumulators for time-weighted averages.
        self._last_t = 0.0
        self._n_queue = 0
        self._n_system = 0
        self._area_queue = 0.0
        self._area_wip = 0.0
        self._max_queue = 0
        self._max_wip = 0
        self._busy_time = 0.0

        # Per-barista slot tracking, for resource-level utilization and the
        # event trace. `_free_slots` is a stack of idle slot indices; popping
        # one on service-start and pushing it back on departure is safe with
        # no locking because SimPy is cooperatively scheduled -- no other
        # process can run between the `yield req` grant and the pop.
        self._free_slots = list(range(num_baristas))
        self._busy_time_by_slot = [0.0] * num_baristas
        self.trace: list[dict] = []

    # ------------------------------------------------------------------
    # Statistics plumbing
    # ------------------------------------------------------------------

    def _advance(self) -> None:
        """Integrate the counters over the interval since the last change.

        Call this *before* mutating _n_queue / _n_system so the elapsed
        interval is credited to the old occupancy levels.
        """
        dt = self.env.now - self._last_t
        if dt > 0:
            self._area_queue += self._n_queue * dt
            self._area_wip += self._n_system * dt
        self._last_t = self.env.now

    def _snapshot(self, event: str) -> None:
        """Record the post-mutation state for the trace, if enabled."""
        if not self.record_trace:
            return
        self.trace.append({
            "t": self.env.now,
            "event": event,
            "queue_len": self._n_queue,
            "wip": self._n_system,
            "busy": [i not in self._free_slots for i in range(self.num_baristas)],
        })

    # ------------------------------------------------------------------
    # Processes
    # ------------------------------------------------------------------

    def customer(self, cid: int):
        arrival_time = self.env.now
        self._advance()
        self._n_queue += 1
        self._n_system += 1
        self._max_queue = max(self._max_queue, self._n_queue)
        self._max_wip = max(self._max_wip, self._n_system)
        self._snapshot("arrival")
        if self.verbose:
            print(f"Customer {cid} arrives at {arrival_time:.2f}")

        with self.baristas.request() as req:
            yield req
            slot = self._free_slots.pop()

            start_time = self.env.now
            self._advance()
            self._n_queue -= 1
            self._snapshot("service_start")
            if self.verbose:
                print(f"Customer {cid} starts service at {start_time:.2f}")

            service_time = self.rng.expovariate(1.0 / self.mean_service_time)
            self._busy_time += service_time
            self._busy_time_by_slot[slot] += service_time
            yield self.env.timeout(service_time)

            end_time = self.env.now
            self._advance()
            self._n_system -= 1
            self._free_slots.append(slot)
            self._snapshot("departure")
            if self.verbose:
                print(f"Customer {cid} leaves at {end_time:.2f}")

        self.records.append(
            CustomerRecord(cid, arrival_time, start_time, end_time, service_time)
        )

    def arrivals(self):
        """Poisson arrivals until the shop stops admitting customers."""
        cid = 0
        while True:
            yield self.env.timeout(self.rng.expovariate(self.arrival_rate))
            if self.env.now >= self.horizon:
                break
            cid += 1
            self.env.process(self.customer(cid))

    # ------------------------------------------------------------------
    # Run + KPIs
    # ------------------------------------------------------------------

    def run(self) -> dict:
        self._snapshot("start")
        self.env.process(self.arrivals())
        self.env.run()  # no `until` -> let every admitted customer finish
        self._advance()  # flush the final interval
        return self.kpis()

    def kpis(self) -> dict:
        n = len(self.records)
        if n == 0:
            return {
                "customers_served": 0,
                "cmax": 0.0,
                "utilization": 0.0,
                "throughput": 0.0,
                "avg_wait": 0.0,
                "max_wait": 0.0,
                "p95_wait": 0.0,
                "avg_queue_length": 0.0,
                "max_queue_length": 0,
                "avg_wip": 0.0,
                "max_wip": 0,
                "avg_sojourn": 0.0,
                "barista_utilization": [0.0] * self.num_baristas,
            }

        cmax = max(r.end_time for r in self.records)
        waits = [r.wait_time for r in self.records]
        sojourns = [r.sojourn_time for r in self.records]

        return {
            "customers_served": n,
            "cmax": cmax,
            # Busy barista-minutes over available barista-minutes.
            "utilization": self._busy_time / (self.num_baristas * cmax),
            "throughput": n / cmax,
            "avg_wait": statistics.fmean(waits),
            "max_wait": max(waits),
            "p95_wait": _percentile(waits, 0.95),
            # Time-weighted, not per-customer.
            "avg_queue_length": self._area_queue / cmax,
            "max_queue_length": self._max_queue,
            "avg_wip": self._area_wip / cmax,
            "max_wip": self._max_wip,
            "avg_sojourn": statistics.fmean(sojourns),
            "barista_utilization": [bt / cmax for bt in self._busy_time_by_slot],
        }

    def validate(self) -> dict:
        """Little's law check: L should equal lambda_eff * W."""
        k = self.kpis()
        if k["customers_served"] == 0:
            return {"littles_law_l": 0.0, "littles_law_lambda_w": 0.0, "abs_error": 0.0}
        lhs = k["avg_wip"]
        rhs = k["throughput"] * k["avg_sojourn"]
        return {
            "littles_law_l": lhs,
            "littles_law_lambda_w": rhs,
            "abs_error": abs(lhs - rhs),
        }


def _percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (numpy's default method)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def summarize(values: list[float]) -> dict:
    """Mean plus a 95% confidence interval across replications.

    Uses the normal approximation for the CI, which is why the default
    replication count is >= 30.
    """
    n = len(values)
    mean = statistics.fmean(values)
    if n < 2:
        return {
            "mean": mean,
            "stdev": 0.0,
            "ci95_half_width": 0.0,
            "ci95_low": mean,
            "ci95_high": mean,
            "min": mean,
            "max": mean,
        }
    sd = statistics.stdev(values)
    half = statistics.NormalDist().inv_cdf(0.975) * sd / math.sqrt(n)
    return {
        "mean": mean,
        "stdev": sd,
        "ci95_half_width": half,
        "ci95_low": mean - half,
        "ci95_high": mean + half,
        "min": min(values),
        "max": max(values),
    }


def run_experiment(
    arrival_rate: float,
    num_baristas: int,
    mean_service_time: float,
    horizon: float,
    reps: int,
    seed: int,
    verbose: bool = False,
) -> dict:
    """Run `reps` independent replications and aggregate the KPIs."""
    # Draw each replication's seed from a master stream so the whole
    # experiment is reproducible from one number and streams don't overlap.
    master = random.Random(seed)
    seeds = [master.randrange(2**32) for _ in range(reps)]

    per_rep = []
    validations = []
    trace = []
    for i, rep_seed in enumerate(seeds):
        sim = BrewLine(
            arrival_rate=arrival_rate,
            num_baristas=num_baristas,
            mean_service_time=mean_service_time,
            horizon=horizon,
            seed=rep_seed,
            verbose=verbose and i == 0,  # only trace the first replication
            record_trace=(i == 0),  # event trace is only kept for replication 0
        )
        result = sim.run()
        per_rep.append({"replication": i, "seed": rep_seed, **result})
        validations.append(sim.validate()["abs_error"])
        if i == 0:
            trace = sim.trace

    # barista_utilization is a per-slot list, not a scalar -- summarized
    # separately below (column-wise) into resource_kpis.
    kpi_names = [
        k for k in per_rep[0] if k not in ("replication", "seed", "barista_utilization")
    ]
    summary = {name: summarize([r[name] for r in per_rep]) for name in kpi_names}

    resource_kpis = {
        "num_baristas": num_baristas,
        "utilization_by_barista": [
            summarize([r["barista_utilization"][i] for r in per_rep])
            for i in range(num_baristas)
        ],
    }

    offered_load = arrival_rate * mean_service_time / num_baristas

    return {
        "model": "BrewLine",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "arrival_rate_per_min": arrival_rate,
            "mean_interarrival_min": 1.0 / arrival_rate,
            "num_baristas": num_baristas,
            "mean_service_time_min": mean_service_time,
            "horizon_min": horizon,
            "replications": reps,
            "master_seed": seed,
            "offered_load_rho": offered_load,
            "stable": offered_load < 1.0,
        },
        "summary": summary,
        "resource_kpis": resource_kpis,
        "trace": trace,
        "replications": per_rep,
        "validation": {
            "littles_law_max_abs_error": max(validations),
            "note": "L vs lambda_eff * W across replications; near zero means "
            "the time-weighted accumulators agree with the customer records.",
        },
    }


def _round(obj, ndigits: int = 6):
    """Recursively round floats so the exported JSON stays readable."""
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, dict):
        return {k: _round(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round(v, ndigits) for v in obj]
    return obj


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BrewLine coffee shop digital twin")
    p.add_argument("--arrival-rate", type=float, default=0.5,
                   help="customers per minute (default: 0.5, i.e. one every 2 min)")
    p.add_argument("--num-baristas", type=int, default=2)
    p.add_argument("--mean-service-time", type=float, default=3.0,
                   help="average minutes per customer")
    p.add_argument("--horizon", type=float, default=480.0,
                   help="minutes during which customers arrive (default: 8h day)")
    p.add_argument("--reps", type=int, default=50,
                   help="number of independent replications")
    p.add_argument("--seed", type=int, default=42, help="master seed")
    p.add_argument("--out", default="brewline_results.json",
                   help="path for the exported JSON")
    p.add_argument("--verbose", action="store_true",
                   help="print an event trace for the first replication")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    results = run_experiment(
        arrival_rate=args.arrival_rate,
        num_baristas=args.num_baristas,
        mean_service_time=args.mean_service_time,
        horizon=args.horizon,
        reps=args.reps,
        seed=args.seed,
        verbose=args.verbose,
    )

    with open(args.out, "w") as f:
        json.dump(_round(results), f, indent=2)

    cfg = results["config"]
    s = results["summary"]
    print(f"BrewLine: {cfg['replications']} replications, "
          f"rho={cfg['offered_load_rho']:.2f}"
          f"{'' if cfg['stable'] else '  *** UNSTABLE (rho >= 1) ***'}")
    print(f"{'KPI':<20}{'mean':>10}  {'95% CI':>22}")
    for name in ("utilization", "throughput", "cmax", "avg_wait", "max_wait",
                 "p95_wait", "avg_queue_length", "avg_wip", "customers_served"):
        st = s[name]
        ci = f"[{st['ci95_low']:.3f}, {st['ci95_high']:.3f}]"
        print(f"{name:<20}{st['mean']:>10.3f}  {ci:>22}")
    print(f"\nLittle's law max abs error: "
          f"{results['validation']['littles_law_max_abs_error']:.2e}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
