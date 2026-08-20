# Closed-form queueing results for validation

A simulation that reproduces a known analytic answer is credible. One that
has never been checked against anything is a program that runs.

Build the simplified, analytically tractable version of the model first,
confirm it matches, and only then add lognormal service, priorities, or
balking. A validation failure then points at the feature you just added.

`scripts/validate_results.py` automates the M/M/c comparison. The formulas
below are for hand-checking and for the cases the script doesn't cover.

## Contents

- [Notation](#notation)
- [Little's law](#littles-law)
- [M/M/1](#mm1)
- [M/M/c](#mmc)
- [M/M/c/K — finite waiting room](#mmck--finite-waiting-room)
- [M/G/1 — general service times](#mg1--general-service-times)
- [Reading a mismatch](#reading-a-mismatch)

## Notation

| Symbol | Meaning |
|---|---|
| λ | arrival rate |
| μ | service rate = 1 / E[S] |
| c | number of parallel servers |
| a = λ/μ | offered load, in erlangs |
| ρ = a/c | per-server utilization |
| L, L_q | mean number in system / in queue (time-weighted) |
| W, W_q | mean time in system / in queue (per-entity) |

All results below are **steady-state**. A terminating simulation starting
empty will land slightly below them; see [Reading a mismatch](#reading-a-mismatch).

Stability requires ρ < 1. At ρ ≥ 1 there is no steady state and no finite
value to compare against — the queue grows without bound and every wait
statistic just reflects how long you ran.

## Little's law

    L = λ·W        and        L_q = λ·W_q

Holds for essentially any stable system regardless of distributions or queue
discipline. That generality is what makes it the universal check.

Use it two ways:

1. **Inside a run** as an identity. `avg_wip` should equal
   `throughput × avg_sojourn` to near machine precision, because both are
   computed from the same events. Agreement of ~1e-15 confirms the
   time-weighted accumulators are consistent with the per-entity records. An
   error of 0.01 is not "close" — it means a change point is missing.
2. **Against theory**, where λ must be the *effective* arrival rate — the
   rate of entities that actually enter. With balking, reneging, or blocking
   that is less than the offered λ, and using the offered rate is a common
   source of a spurious mismatch.

## M/M/1

    ρ = λ/μ
    L   = ρ / (1 − ρ)
    L_q = ρ² / (1 − ρ)
    W   = 1 / (μ − λ)
    W_q = ρ / (μ − λ)
    P(n in system) = (1 − ρ)ρⁿ

Worked example, λ = 0.5, μ = 1/3 (mean service 3):
ρ = 1.5. **Unstable** — no steady state. This is the single most common
parameter mistake: one server, arrivals every 2 minutes, 3 minutes of work
each. Check ρ before running anything.

Worked example, λ = 0.2, μ = 1/3: ρ = 0.6, L = 1.5, L_q = 0.9,
W = 7.5, W_q = 4.5.

Note how violently these blow up near ρ = 1: L_q is 0.9 at ρ = 0.6, 8.1 at
ρ = 0.9, and 98 at ρ = 0.99. Halving idle capacity does not halve the queue.

## M/M/c

Needs the Erlang C formula. First the probability the system is empty:

    P₀ = [ Σ(k=0..c−1) aᵏ/k!  +  a^c / (c!(1−ρ)) ]⁻¹

Then:

    L_q = P₀ · a^c · ρ / (c! (1−ρ)²)
    W_q = L_q / λ
    W   = W_q + 1/μ
    L   = L_q + a
    utilization = ρ
    throughput  = λ

In Python:

```python
import math

def mmc(lam, mean_service, c):
    mu = 1 / mean_service
    a = lam / mu
    rho = a / c
    assert rho < 1, f"unstable: rho={rho:.3f}"
    denom = sum(a**k / math.factorial(k) for k in range(c))
    denom += a**c / (math.factorial(c) * (1 - rho))
    p0 = 1 / denom
    lq = p0 * a**c * rho / (math.factorial(c) * (1 - rho)**2)
    wq = lq / lam
    return {"rho": rho, "Lq": lq, "Wq": wq,
            "L": lq + a, "W": wq + mean_service, "throughput": lam}
```

Worked example, λ = 0.5, μ = 1/3, c = 2 (the template's defaults):

| Quantity | Value |
|---|---|
| a | 1.5 |
| ρ | 0.75 |
| P₀ | 1/7 ≈ 0.142857 |
| L_q | 1.9286 |
| W_q | 3.8571 |
| L | 3.4286 |
| W | 6.8571 |
| throughput | 0.5 |

Two servers at ρ = 0.75 give L_q = 1.93, while one server at ρ = 0.75 gives
L_q = 2.25 — pooling helps, and the benefit grows with c. Worth knowing when
the recommendation is "one shared queue" versus "a line per server".

## M/M/c/K — finite waiting room

At most K in the system; arrivals finding it full are lost. Always stable,
even at ρ ≥ 1, because the system sheds load.

With c = 1 and K total capacity:

    P₀ = (1 − ρ) / (1 − ρ^(K+1))          for ρ ≠ 1
    P_block = P₀ · ρ^K
    λ_eff = λ(1 − P_block)
    L = Σ(n=0..K) n·P₀·ρⁿ
    W = L / λ_eff                          ← Little's law with λ_eff

The thing to carry into the simulation: **throughput is λ_eff, not λ**. If
your model blocks arrivals and you compare throughput against λ, the gap
looks like a bug when it is the model working. Report the loss probability
next to throughput.

## M/G/1 — general service times

When service is not exponential, the Pollaczek–Khinchine formula gives the
mean wait from just the mean and variance of service time:

    W_q = ρ·E[S]·(1 + CV²) / (2(1 − ρ))

where CV = sd(S) / E[S]. Then W = W_q + E[S], L_q = λW_q, L = λW.

Sanity checks on the formula:
- CV = 1 (exponential) reduces to the M/M/1 result.
- CV = 0 (deterministic, M/D/1) halves the exponential wait.

This is the most useful analytic result in practice, because it isolates the
effect of *variability* from the effect of *load*. Waiting scales with
(1 + CV²), so cutting service-time variability in half — same mean — cuts the
queue substantially. When a study's recommendation is "standardise the
process" rather than "add capacity", this is why.

Use it to validate a lognormal-service model: switch the template to
lognormal, keep the mean, and check the simulated W_q against P-K.

## Reading a mismatch

**Simulated mean slightly below analytic, utilization matches.** Expected for
a terminating simulation. The system starts empty, so the transient drags
congestion metrics down. Confirm by lengthening the horizon — the gap should
shrink. If it doesn't shrink, it isn't the transient.

**Simulated mean above analytic.** A terminating run should not exceed a
steady-state value. Suspects, roughly in order:
- Time-weighted average computed by averaging arrival-triggered samples
  (biases upward — see `kpis.md`).
- Utilization missing the `× c` in its denominator.
- `expovariate` given a mean where it wanted a rate.

**Utilization matches but waits are far off.** The load is right and the
queueing dynamics are wrong. Look at queue discipline and at whether the
`_advance()` calls cover every change point.

**Utilization itself is wrong.** The denominator, or the arrival rate units.
Utilization should equal ρ = λ·E[S]/c almost exactly in any stable run; it is
the most robust of all these checks, so a mismatch here means a parameter
problem rather than a statistics problem.

**Everything is off by a large factor.** Units. A rate/mean swap scales the
offered load by the square of the parameter, and mixing minutes with hours
scales it by 60. Print ρ at startup and eyeball it against intuition before
trusting anything downstream.
