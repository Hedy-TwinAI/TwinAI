/**
 * Walks the raw (unresampled) BrewLine event trace to derive, at any
 * simulation time `t`, which customers are waiting (and in what order) and
 * which barista slots are occupied by whom.
 *
 * Unlike `resampleTrace`'s evenly-spaced carry-forward grid -- which can
 * skip several events between two grid points -- this replays every event
 * up to `t`, so per-customer position never "teleports" between frames.
 *
 * Relies on `baristas` being a plain FIFO `simpy.Resource` (no priorities):
 * `service_start` events are guaranteed to occur in the same order as their
 * `arrival` events, so queue rank can be derived by shifting a FIFO array
 * rather than needing an explicit queue-position field in the trace.
 */
export function createCustomerReconstructor(rawTrace) {
  const trace = rawTrace ?? [];

  let cursor = 0;
  let lastT = -Infinity;
  let waitingQueue = [];
  let stationOccupant = new Map(); // slot -> cid
  let customers = new Map(); // cid -> { state: "waiting" | "served" | "departed", slot, since }

  function reset() {
    cursor = 0;
    lastT = -Infinity;
    waitingQueue = [];
    stationOccupant = new Map();
    customers = new Map();
  }

  function applyEvent(e) {
    if (e.event === "arrival") {
      waitingQueue.push(e.cid);
      customers.set(e.cid, { state: "waiting", slot: null, since: e.t });
    } else if (e.event === "service_start") {
      const next = waitingQueue.shift();
      if (next !== e.cid && typeof console !== "undefined") {
        console.assert(
          false,
          `reconstructCustomers: FIFO violation, expected cid ${next} but service_start was for ${e.cid}`,
        );
      }
      stationOccupant.set(e.slot, e.cid);
      customers.set(e.cid, { state: "served", slot: e.slot, since: e.t });
    } else if (e.event === "departure") {
      stationOccupant.delete(e.slot);
      customers.set(e.cid, { state: "departed", slot: e.slot, since: e.t });
    }
  }

  function advanceTo(t) {
    if (t < lastT) reset();
    while (cursor < trace.length && trace[cursor].t <= t) {
      applyEvent(trace[cursor]);
      cursor += 1;
    }
    lastT = t;
  }

  function getSnapshot() {
    // Drop departed customers once they've had one tick to be observed, so
    // the caller can animate a brief fade/exit before the slot is reused.
    for (const [cid, info] of customers) {
      if (info.state === "departed" && lastT - info.since > 0) {
        customers.delete(cid);
      }
    }
    return {
      waitingOrder: [...waitingQueue],
      stations: [...stationOccupant.entries()],
      customers: new Map(customers),
    };
  }

  return { advanceTo, getSnapshot };
}

const STATION_SPACING = 1.6;
const QUEUE_START_Z = 1.3;
const QUEUE_SPACING = 1.1;

/**
 * Pure mapping from a reconstructor snapshot to world-space targets, in
 * meter-scale units (matching the humanoid model's native scale). The queue
 * is a single-file line running perpendicular to the counter (rank 0 is the
 * front of the line, closest to the stations) so it reads as "a line",
 * rather than running alongside the stations where it would look like a
 * crowd loitering at the counter.
 */
export function layoutPositions(snapshot, numBaristas) {
  const targets = new Map();

  snapshot.waitingOrder.forEach((cid, rank) => {
    targets.set(cid, { x: 0, y: 0, z: QUEUE_START_Z + rank * QUEUE_SPACING });
  });

  const stationX = (slot) => (slot - (numBaristas - 1) / 2) * STATION_SPACING;
  for (const [slot, cid] of snapshot.stations) {
    targets.set(cid, { x: stationX(slot), y: 0, z: 0 });
  }

  return targets;
}
