function formatValue(value, unit) {
  if (value == null || Number.isNaN(value)) return "—";
  if (unit === "pct") return `${(value * 100).toFixed(1)}%`;
  if (unit === "min") return `${value.toFixed(1)} min`;
  if (unit === "per_min") return `${value.toFixed(2)}/min`;
  return value.toFixed(1);
}

export default function KpiCard({ label, value, unit, ci }) {
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-5 py-4">
      <div className="text-sm text-[var(--text-muted)]">{label}</div>
      <div className="mt-1 text-3xl font-semibold text-[var(--text-primary)]">
        {formatValue(value, unit)}
      </div>
      {ci && (
        <div className="mt-1 text-xs text-[var(--text-secondary)]">
          95% CI [{formatValue(ci.ci95_low, unit)}, {formatValue(ci.ci95_high, unit)}]
        </div>
      )}
    </div>
  );
}
