const FIELDS = [
  { key: "arrival_rate", label: "Arrival rate", unit: "customers/min", min: 0.05, max: 5, step: 0.05 },
  { key: "num_baristas", label: "Baristas", unit: "servers", min: 1, max: 10, step: 1 },
  { key: "mean_service_time", label: "Mean service time", unit: "min", min: 0.5, max: 20, step: 0.5 },
];

export default function ControlPanel({ knobs, onChange, isLoading, error }) {
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-medium text-[var(--text-primary)]">Simulation inputs</h3>
        {isLoading && <span className="text-xs text-[var(--text-muted)]">recomputing…</span>}
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {FIELDS.map(({ key, label, unit, min, max, step }) => (
          <label key={key} className="text-xs text-[var(--text-secondary)]">
            <div className="mb-1 flex items-baseline justify-between">
              <span>{label}</span>
              <span className="text-[var(--text-muted)]">
                {knobs[key]} {unit}
              </span>
            </div>
            <input
              type="range"
              min={min}
              max={max}
              step={step}
              value={knobs[key]}
              onChange={(e) =>
                onChange({ ...knobs, [key]: Number(e.target.value) })
              }
              className="w-full accent-[var(--series-queue)]"
            />
          </label>
        ))}
      </div>
      {error && <div className="mt-3 text-xs text-[var(--status-critical)]">{error}</div>}
    </div>
  );
}
