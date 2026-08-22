/**
 * Resample an irregular event trace onto an evenly-spaced time grid using
 * step/carry-forward interpolation (the last known state holds until the
 * next event) -- matches BrewLine's own time-weighted KPI convention.
 */
export function resampleTrace(trace, numPoints = 300) {
  if (!trace || trace.length === 0) return [];

  const cmax = trace[trace.length - 1].t;
  if (cmax <= 0) return [{ t: 0, queue_len: 0, wip: 0, busy: trace[0]?.busy ?? [] }];

  const dt = cmax / (numPoints - 1);
  const grid = [];
  let cursor = 0;

  for (let k = 0; k < numPoints; k++) {
    const t = k * dt;
    while (cursor + 1 < trace.length && trace[cursor + 1].t <= t) {
      cursor += 1;
    }
    const entry = trace[cursor];
    grid.push({ t, queue_len: entry.queue_len, wip: entry.wip, busy: entry.busy });
  }

  return grid;
}
