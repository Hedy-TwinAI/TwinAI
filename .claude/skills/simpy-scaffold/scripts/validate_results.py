#!/usr/bin/env python3
"""Validate a simulation results JSON exported by a simpy-scaffold model.

Three layers of checking, from cheapest to most convincing:

  1. Schema    -- are the required blocks and per-KPI statistics present?
  2. Internal  -- do the summary means match the replication rows? Is
                  utilization in [0, 1]? Is Little's law satisfied? Are
                  there enough replications for the normal-approximation CI?
  3. Analytic  -- if the config describes an M/M/c system, compute the true
                  steady-state values and check them against the reported
                  confidence intervals.

Layer 3 is the one that actually convinces a reviewer, because it compares
against an answer the simulation had no way to see.

Usage:
    python validate_results.py results.json
    python validate_results.py results.json --quiet   # only problems

Exit status is 0 when nothing failed, 1 otherwise, so this can gate CI.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys

# Terminating simulations start empty, so their means sit below the
# steady-state analytic values. Anything under this factor is treated as
# ordinary transient bias rather than a defect.
TRANSIENT_TOLERANCE = 0.35

# Little's law is an identity over the same run, so agreement should be near
# machine precision. Anything above this means a missing change point.
LITTLES_LAW_TOLERANCE = 1e-6

REQUIRED_TOP_LEVEL = ("model", "config", "summary", "replications")
REQUIRED_STATS = ("mean", "stdev", "ci95_low", "ci95_high")


class Report:
    """Collects pass/warn/fail lines and decides the exit status."""

    def __init__(self, quiet: bool = False):
        self.quiet = quiet
        self.failures = 0
        self.warnings = 0

    def ok(self, msg: str) -> None:
        if not self.quiet:
            print(f"  PASS  {msg}")

    def warn(self, msg: str) -> None:
        self.warnings += 1
        print(f"  WARN  {msg}")

    def fail(self, msg: str) -> None:
        self.failures += 1
        print(f"  FAIL  {msg}")

    def section(self, title: str) -> None:
        if not self.quiet:
            print(f"\n{title}")


def find_key(mapping: dict, *substrings: str) -> str | None:
    """Find the first key containing any of `substrings`.

    Models name things for their domain -- `num_baristas`, `num_nurses`,
    `customers_served` -- so match loosely rather than demanding one
    vocabulary.
    """
    for sub in substrings:
        for key in mapping:
            if sub in key:
                return key
    return None


# ----------------------------------------------------------------------
# Layer 1: schema
# ----------------------------------------------------------------------

def check_schema(data: dict, rep: Report) -> bool:
    rep.section("Schema")
    missing = [k for k in REQUIRED_TOP_LEVEL if k not in data]
    if missing:
        rep.fail(f"missing top-level keys: {', '.join(missing)}")
        return False
    rep.ok("required top-level keys present")

    if not data["replications"]:
        rep.fail("`replications` is empty -- nothing was simulated")
        return False

    incomplete = [
        name for name, stats in data["summary"].items()
        if not all(s in stats for s in REQUIRED_STATS)
    ]
    if incomplete:
        rep.fail(f"summary entries missing {REQUIRED_STATS}: {', '.join(incomplete)}")
    else:
        rep.ok(f"all {len(data['summary'])} summary KPIs carry mean + stdev + CI")

    if "validation" not in data:
        rep.warn("no `validation` block -- add a Little's law check to the model")
    return True


# ----------------------------------------------------------------------
# Layer 2: internal consistency
# ----------------------------------------------------------------------

def check_internal(data: dict, rep: Report) -> None:
    rep.section("Internal consistency")
    cfg = data["config"]
    reps = data["replications"]
    summary = data["summary"]

    n = len(reps)
    if n < 30:
        rep.warn(f"only {n} replications; the normal-approximation CI wants >= 30")
    else:
        rep.ok(f"{n} replications")

    # Summary means must actually come from the replication rows.
    drifted = []
    for name, stats in summary.items():
        values = [r[name] for r in reps if name in r]
        if len(values) != n:
            continue
        recomputed = statistics.fmean(values)
        if not math.isclose(recomputed, stats["mean"], rel_tol=1e-3, abs_tol=1e-6):
            drifted.append(f"{name} (summary {stats['mean']:.4f} vs "
                           f"recomputed {recomputed:.4f})")
    if drifted:
        rep.fail("summary means disagree with replication rows: " + "; ".join(drifted))
    else:
        rep.ok("summary means reproduce from the replication rows")

    # Utilization is a ratio of busy time to available time.
    util_key = find_key(summary, "utilization", "utilisation")
    if util_key:
        bad = [r["replication"] for r in reps
               if not 0.0 <= r.get(util_key, 0.0) <= 1.0]
        if bad:
            rep.fail(f"{util_key} outside [0, 1] in replications {bad[:5]} -- "
                     "the denominator is probably wrong "
                     "(needs num_servers * horizon, not just horizon)")
        else:
            rep.ok(f"{util_key} within [0, 1] in every replication")

    # Seeds must differ, or the "replications" are one run repeated.
    seeds = [r.get("seed") for r in reps if "seed" in r]
    if seeds and len(set(seeds)) < len(seeds):
        rep.fail("duplicate seeds across replications -- runs are not independent")
    elif seeds:
        rep.ok("all replication seeds distinct")
    else:
        rep.warn("replications carry no `seed` -- results are not reproducible")

    # Stability. Everything downstream is meaningless if rho >= 1.
    rho_key = find_key(cfg, "rho", "offered_load", "utilization")
    if rho_key:
        rho = cfg[rho_key]
        if rho >= 1.0:
            rep.fail(f"offered load rho = {rho:.3f} >= 1: the queue is unstable, "
                     "so wait and queue-length KPIs grow with the horizon and "
                     "do not estimate anything")
        elif rho > 0.95:
            rep.warn(f"rho = {rho:.3f} is very close to 1; expect wide CIs and "
                     "slow convergence")
        else:
            rep.ok(f"offered load rho = {rho:.3f} (stable)")

    # Little's law.
    ll = data.get("validation", {})
    err_key = find_key(ll, "littles_law_max_abs_error", "abs_error") if ll else None
    if err_key:
        err = ll[err_key]
        if err > LITTLES_LAW_TOLERANCE:
            rep.fail(f"Little's law error {err:.2e} exceeds {LITTLES_LAW_TOLERANCE:.0e}: "
                     "L != lambda_eff * W, so a time-weighted accumulator is "
                     "missing a change point")
        else:
            rep.ok(f"Little's law holds to {err:.2e}")

    # Time-weighted averages should not exceed their observed maxima.
    for avg_sub, max_sub in (("avg_queue_length", "max_queue_length"),
                             ("avg_wip", "max_wip")):
        if avg_sub in summary and max_sub in summary:
            if summary[avg_sub]["mean"] > summary[max_sub]["mean"] + 1e-9:
                rep.fail(f"{avg_sub} mean exceeds {max_sub} mean -- "
                         "the time-weighted accumulator is wrong")
            else:
                rep.ok(f"{avg_sub} <= {max_sub}")


# ----------------------------------------------------------------------
# Layer 3: analytic comparison
# ----------------------------------------------------------------------

def mmc_analytics(lam: float, mean_service: float, c: int) -> dict | None:
    """Steady-state M/M/c results, or None if the system is unstable."""
    mu = 1.0 / mean_service
    a = lam / mu           # offered load in erlangs
    rho = a / c            # per-server utilization
    if rho >= 1.0:
        return None

    # Erlang C
    denom = sum(a**k / math.factorial(k) for k in range(c))
    denom += a**c / (math.factorial(c) * (1 - rho))
    p0 = 1.0 / denom
    lq = p0 * a**c * rho / (math.factorial(c) * (1 - rho) ** 2)
    wq = lq / lam

    return {
        "utilization": rho,
        "throughput": lam,
        "avg_queue_length": lq,
        "avg_wait": wq,
        "avg_wip": lq + a,
        "avg_sojourn": wq + mean_service,
    }


def check_analytic(data: dict, rep: Report) -> None:
    rep.section("Analytic comparison (M/M/c closed form)")
    cfg = data["config"]

    lam_key = find_key(cfg, "arrival_rate")
    svc_key = find_key(cfg, "mean_service_time", "mean_service")
    srv_key = find_key(cfg, "num_servers", "num_")

    if not (lam_key and svc_key and srv_key):
        rep.warn("config does not expose arrival rate / mean service time / server "
                 "count under recognisable names -- skipping analytic check")
        return

    lam, svc, c = cfg[lam_key], cfg[svc_key], int(cfg[srv_key])
    theory = mmc_analytics(lam, svc, c)
    if theory is None:
        rep.warn("rho >= 1: no steady state exists, so there is nothing to "
                 "compare against")
        return

    if not rep.quiet:
        print(f"  (M/M/{c} with lambda={lam}, E[S]={svc})")

    summary = data["summary"]
    for name, expected in theory.items():
        key = find_key(summary, name)
        if key is None:
            continue
        stats = summary[key]
        mean, lo, hi = stats["mean"], stats["ci95_low"], stats["ci95_high"]

        if lo <= expected <= hi:
            rep.ok(f"{key}: {mean:.4f} [{lo:.4f}, {hi:.4f}] contains "
                   f"analytic {expected:.4f}")
        elif expected > hi:
            # Simulated mean below the analytic value: the empty start drags
            # a terminating run's average down, which is expected.
            shortfall = (expected - mean) / expected if expected else 0.0
            if shortfall <= TRANSIENT_TOLERANCE:
                rep.warn(f"{key}: {mean:.4f} [{lo:.4f}, {hi:.4f}] is "
                         f"{shortfall:.0%} below analytic {expected:.4f} -- "
                         "consistent with starting empty; lengthen the horizon "
                         "or drop a warm-up to close the gap")
            else:
                rep.fail(f"{key}: {mean:.4f} [{lo:.4f}, {hi:.4f}] is "
                         f"{shortfall:.0%} below analytic {expected:.4f} -- "
                         "too large to be transient bias")
        else:
            rep.fail(f"{key}: {mean:.4f} [{lo:.4f}, {hi:.4f}] exceeds analytic "
                     f"{expected:.4f} -- a terminating run should not "
                     "over-estimate a steady-state value")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("results", help="path to the exported results JSON")
    p.add_argument("--quiet", action="store_true",
                   help="print only warnings and failures")
    args = p.parse_args()

    try:
        with open(args.results) as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"error: no such file: {args.results}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"error: {args.results} is not valid JSON: {e}", file=sys.stderr)
        return 1

    rep = Report(quiet=args.quiet)
    print(f"Validating {args.results}")

    if check_schema(data, rep):
        check_internal(data, rep)
        check_analytic(data, rep)

    print(f"\n{rep.failures} failure(s), {rep.warnings} warning(s)")
    return 1 if rep.failures else 0


if __name__ == "__main__":
    sys.exit(main())
