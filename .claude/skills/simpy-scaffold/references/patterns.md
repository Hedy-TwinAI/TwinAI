# SimPy patterns beyond the basic queue

Recipes for the features real systems have. Add them one at a time, and
re-run validation after each — that way a failure points at the feature you
just added rather than at the whole model.

Every pattern that changes queue occupancy must call `_advance()` before
mutating the counters, or its interval drops out of the time-weighted
averages. See `kpis.md`.

## Contents

- [Choosing a service distribution](#choosing-a-service-distribution)
- [Time-varying arrivals](#time-varying-arrivals)
- [Priority queues](#priority-queues)
- [Balking and reneging](#balking-and-reneging)
- [Multi-stage flows](#multi-stage-flows)
- [Multiple resource types](#multiple-resource-types)
- [Breakdowns and shifts](#breakdowns-and-shifts)
- [Batching](#batching)
- [Finite waiting room](#finite-waiting-room)
- [Which SimPy resource class](#which-simpy-resource-class)

## Choosing a service distribution

Exponential is the default because it has a closed form to validate against,
but it implies a coefficient of variation of exactly 1 and a mode at zero —
it says most jobs are near-instant. That is wrong for most human work.

```python
# Exponential: memoryless, CV = 1. Validate against M/M/c.
self.rng.expovariate(1.0 / mean)

# Lognormal: realistic for human service times. cv is the coefficient
# of variation (sd / mean); 0.3-0.5 is typical for a trained operator.
sigma = math.sqrt(math.log(1 + cv**2))
mu = math.log(mean) - sigma**2 / 2
self.rng.lognormvariate(mu, sigma)

# Triangular: when you only have expert opinion (min, mode, max).
self.rng.triangular(low, high, mode)

# Deterministic: automated steps with fixed cycle time.
mean
```

Variability, not just the mean, drives congestion. The Pollaczek–Khinchine
formula (see `validation.md`) shows M/G/1 waiting is proportional to
(1 + CV²), so halving the CV nearly halves the wait at the same mean. That
result is often the most actionable thing a study produces — "make service
more consistent" beats "hire more staff" surprisingly often.

Validate with exponential first, then switch. If the switch moves waits in
the direction (1 + CV²) predicts, the model is behaving.

## Time-varying arrivals

A coffee shop is not Poisson-stationary — there is a morning peak. Use
thinning: generate at the *maximum* rate and reject in proportion to the
current rate. This is correct for any rate function, unlike the tempting
approach of changing the rate mid-stream, which distorts the process at
each change point.

```python
def arrivals(self):
    lam_max = max(self.rate_by_hour)
    eid = 0
    while True:
        yield self.env.timeout(self.rng.expovariate(lam_max))
        if self.env.now >= self.horizon:
            break
        current = self.rate_by_hour[int(self.env.now // 60) % len(self.rate_by_hour)]
        if self.rng.random() < current / lam_max:   # thinning
            eid += 1
            self.env.process(self.entity(eid))
```

With time-varying arrivals there is no steady state, so closed-form
validation no longer applies. Validate the stationary version first.

## Priority queues

`PriorityResource` serves the lowest priority value first but lets an
in-progress service finish. `PreemptiveResource` interrupts it.

```python
self.servers = simpy.PriorityResource(self.env, capacity=c)

with self.servers.request(priority=prio) as req:   # lower = more urgent
    yield req
```

For preemption, handle the interrupt or the process dies silently:

```python
self.servers = simpy.PreemptiveResource(self.env, capacity=c)

with self.servers.request(priority=prio, preempt=True) as req:
    yield req
    try:
        yield self.env.timeout(service_time)
    except simpy.Interrupt as interrupt:
        done = self.env.now - interrupt.cause.usage_since
        # Decide explicitly: resume the remainder, or restart from scratch?
        # They give materially different answers. Record which you chose.
```

Report KPIs **per priority class** as well as overall. The whole point of
priority is that classes get different service; a pooled average conceals
exactly the effect being modelled.

## Balking and reneging

**Balking** — refuses to join a queue that is already too long:

```python
def entity(self, eid):
    if len(self.servers.queue) >= self.balk_threshold:
        self.balked += 1
        return          # no _advance needed: never joined
    ...
```

**Reneging** — joins, then gives up after waiting too long. Race the resource
request against a timeout:

```python
with self.servers.request() as req:
    patience = self.rng.expovariate(1.0 / self.mean_patience)
    result = yield req | self.env.timeout(patience)
    if req not in result:
        self._advance()
        self._n_queue -= 1
        self._n_system -= 1
        self.reneged += 1
        return
    # ... served normally
```

Once entities leave without service, **throughput is no longer the arrival
rate** and "avg wait" over served entities alone is a survivorship-biased
statistic — the impatient ones are exactly the ones who waited least.
Always report the abandonment rate next to it, and consider reporting wait
distributions for served and abandoned separately.

Balking and reneging also stabilise an otherwise unstable system: with
abandonment, ρ ≥ 1 no longer means unbounded queues, because the queue sheds
load. That is realistic, but it means the stability check is now about
whether the abandonment rate is acceptable rather than whether ρ < 1.

## Multi-stage flows

Order at the register, then wait for the barista. Just request resources in
sequence within one process — and record a timestamp at every transition, or
you cannot attribute the delay to a stage.

```python
def entity(self, eid):
    t_arrive = self.env.now
    with self.cashiers.request() as req:
        yield req
        t_pay_start = self.env.now
        yield self.env.timeout(self.rng.expovariate(1 / self.mean_pay))
    t_pay_end = self.env.now
    with self.baristas.request() as req:
        yield req
        t_brew_start = self.env.now
        yield self.env.timeout(self.rng.expovariate(1 / self.mean_brew))
    t_done = self.env.now
```

Keep separate queue accumulators per stage. The interesting output is which
stage is the bottleneck, and a single pooled queue length cannot answer that.

Blocking matters here: if stage 2 has finite buffer space, an entity that has
finished stage 1 but cannot enter stage 2 still occupies its stage-1 server.
Model that by acquiring the stage-2 slot *before* releasing stage 1
(nest the `with` blocks) — otherwise you understate congestion.

## Multiple resource types

When a task needs two things at once (a nurse *and* a room), nest the
requests. Always acquire in a **globally consistent order** across all
process types, or you can deadlock:

```python
with self.nurses.request() as n_req:
    yield n_req
    with self.rooms.request() as r_req:      # same order everywhere
        yield r_req
        yield self.env.timeout(service_time)
```

If one process takes nurse-then-room and another takes room-then-nurse, both
can hold half of what the other needs and the simulation stalls with no
error message.

## Breakdowns and shifts

A background process that seizes the whole resource models downtime or a
closed window:

```python
def breakdowns(self):
    while True:
        yield self.env.timeout(self.rng.expovariate(1 / self.mtbf))
        with self.servers.request(priority=-1, preempt=True) as req:
            yield req
            yield self.env.timeout(self.rng.expovariate(1 / self.mttr))
```

This needs `PreemptiveResource`. Decide whether downtime counts as available
time in the utilization denominator and state the choice — it changes the
headline number, and both conventions are in common use.

## Batching

Serve a group at once (a shuttle, an oven, a batch job). Use a `Store` as the
holding area:

```python
def batcher(self):
    while True:
        batch = [yield self.holding.get() for _ in range(self.batch_size)]
        yield self.env.timeout(self.batch_service_time)
```

The subtlety: a partial batch waits forever if arrivals stop. Real systems
have a timeout that dispatches a partial batch, and a terminating simulation
needs one or it will not drain. Race the fill against a max-hold timer.

## Finite waiting room

A queue capped at K (M/M/c/K). Use the balking pattern with a fixed
threshold, and report the **loss probability** — with blocking, throughput is
λ·(1 − P_block), not λ, and comparing throughput against λ will look like a
bug when it is the model working correctly. `validation.md` has the closed
form for M/M/c/K.

## Which SimPy resource class

| Class | Use for |
|---|---|
| `Resource` | Interchangeable servers, FIFO. The default. |
| `PriorityResource` | Priority classes, non-preemptive. |
| `PreemptiveResource` | Urgent work interrupts in-progress work; breakdowns. |
| `Container` | Continuous or countable bulk stock (fuel, cash, inventory). |
| `Store` | Discrete distinguishable items (specific parts, batching buffers). |
| `FilterStore` | Items where the taker cares which one it gets. |

`Resource` covers most queueing studies. Reach for `Container` when the thing
being consumed is an amount rather than a server, and `Store` when identity
matters.
