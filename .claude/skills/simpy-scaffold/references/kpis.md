# KPI definitions and how to accumulate them correctly

Every KPI here is cheap once the right accumulators exist. The expensive part
is getting the *kind* of average right, so start there.

## Contents

- [Two kinds of average](#two-kinds-of-average)
- [The accumulator pattern](#the-accumulator-pattern)
- [KPI reference](#kpi-reference)
- [Utilization: choosing a denominator](#utilization-choosing-a-denominator)
- [Across replications](#across-replications)
- [Warm-up for steady-state studies](#warm-up-for-steady-state-studies)

## Two kinds of average

**Per-entity (observation-based).** One number per entity, averaged over
entities. Wait time, sojourn time, service time. `statistics.fmean(waits)` is
exactly right.

**Time-weighted (time-persistent).** A level that exists at every instant.
Queue length, WIP, number of busy servers. The average is an integral:

    avg = (1/T) * ∫₀ᵀ level(t) dt

Averaging a list of samples of a level is wrong unless the samples are evenly
spaced in time. The tempting version — record `len(queue)` whenever someone
arrives — is biased *upward*, because arrivals cluster where the queue is
already long. On a moderately loaded M/M/2 this can be off by 30% or more,
and nothing in the output looks wrong.

Little's law is the fastest way to catch it: L = λ·W ties a time-weighted
quantity to a per-entity one, so if your accumulator is broken the identity
stops holding.

## The accumulator pattern

Maintain the level, the running integral, and the time of the last change.
Integrate *before* mutating the level, so each interval is credited to the
value that actually held during it:

```python
def _advance(self):
    dt = self.env.now - self._last_t
    if dt > 0:
        self._area_queue += self._n_queue * dt
        self._area_wip += self._n_system * dt
    self._last_t = self.env.now
```

Call it at every change point:

| Event | Call, then... |
|---|---|
| Entity arrives | `_advance()`; `n_queue += 1`; `n_system += 1` |
| Service starts | `_advance()`; `n_queue -= 1` |
| Service ends | `_advance()`; `n_system -= 1` |
| Entity balks / reneges | `_advance()`; `n_queue -= 1`; `n_system -= 1` |
| End of run | `_advance()` to flush the final interval |

The failure mode when you add a feature later is forgetting one of these,
which drops that interval from the integral. Little's law catches it.

At the end, `avg_queue_length = area_queue / T`.

Busy server-time is simpler: accumulate each service duration as you draw it.
`self._busy_time += service_time`. That is exact and needs no integration.

## KPI reference

Let `n` = entities completed, `T` = observation window, `c` = servers.

| KPI | Formula | Kind |
|---|---|---|
| Utilization | `busy_time / (c * T)` | ratio |
| Throughput | `n / T` | rate |
| Cmax (makespan) | `max(end_time)` | scalar |
| Avg wait | `mean(start - arrival)` | per-entity |
| Max wait | `max(start - arrival)` | per-entity |
| p95 wait | 95th percentile of waits | per-entity |
| Avg queue length | `area_queue / T` | time-weighted |
| Max queue length | running max of `n_queue` | scalar |
| Avg WIP | `area_wip / T` | time-weighted |
| Max WIP | running max of `n_system` | scalar |
| Avg sojourn | `mean(end - arrival)` | per-entity |

WIP = queue + in service. Keeping both WIP and queue length lets a reader
separate "waiting" congestion from "being worked on".

Report p95 wait alongside the mean. Service-level targets are almost always
expressed as a tail ("90% of callers wait under 30 seconds"), and on a
heavy-tailed queue the mean hides the tail badly.

Max wait and max queue length are single order statistics, so they are much
noisier across replications than the means. Expect wide CIs and don't
over-read a single run's maximum.

## Utilization: choosing a denominator

`busy_time / (c * T)` requires deciding what `T` is, and the choice changes
the number:

- **Cmax** — the whole observed period including the drain tail after
  arrivals stop. The tail is quiet, so this slightly *deflates* utilization.
- **Arrival horizon** — the period the system was actually admitting work.
  Closer to "how busy were the staff while open".

Either is defensible; picking silently is not. Export both `cmax` and
`horizon` in the results JSON so a reader can recompute, and say which one
the headline number uses.

Multiplying by `c` is what makes this a *per-server* utilization in [0, 1].
Omitting it gives you mean busy servers instead — a legitimate quantity, but
not a ratio, and it will exceed 1. `validate_results.py` flags this.

For multiple resource pools, report utilization per pool. An aggregate hides
the bottleneck, which is usually the only thing anyone wanted to know.

## Across replications

Each replication yields one value per KPI. Aggregate across them:

```python
mean = statistics.fmean(values)
half = statistics.NormalDist().inv_cdf(0.975) * statistics.stdev(values) / sqrt(n)
```

The normal approximation is why the default is 30+ replications. For fewer,
use a t quantile with n−1 degrees of freedom instead — with 10 replications
the t multiplier is 2.26 rather than 1.96, so the normal version understates
the interval by about 15%.

Report the interval, not just the mean. A KPI whose CI spans 2× is not
evidence for anything, and the fix is more replications, not more commentary.

Comparing two configurations: if the CIs overlap substantially you cannot
call a winner. Use common random numbers — the same seed sequence for both
configurations — so the comparison isn't fighting sampling noise it didn't
need to.

## Warm-up for steady-state studies

A terminating model (opens, closes, drains) needs no warm-up: starting empty
is part of the thing being measured, and the replication mean is the honest
answer for the period.

A steady-state model does. The empty start biases every congestion metric
downward. Two workable approaches:

1. **Discard a warm-up.** Reset all accumulators at time `t_warmup`, keeping
   the entities already in the system. Pick `t_warmup` by plotting WIP over
   time across a few pilot runs and finding where it flattens (Welch's
   procedure). Resetting is fiddly — the accumulators, the max trackers, and
   the record list all need it, and the entities in progress must not be
   double counted.
2. **Run long past the transient** and state that the residual bias is
   negligible relative to the CI. Simpler and often sufficient; it just needs
   to be an explicit claim rather than an omission.

Signature of an undiagnosed transient: simulated congestion metrics sitting
consistently below the analytic values while utilization matches. That is
exactly the pattern `validate_results.py` reports as a transient warning.
