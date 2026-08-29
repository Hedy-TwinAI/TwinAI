import "./KpiCard.css";

function formatValue(value, unit) {
  if (value == null || Number.isNaN(value)) return "—";
  if (unit === "pct") return `${(value * 100).toFixed(1)}%`;
  if (unit === "min") return `${value.toFixed(1)} min`;
  if (unit === "per_min") return `${value.toFixed(2)}/min`;
  return value.toFixed(1);
}

export default function KpiCard({ label, value, unit, ci }) {
  return (
    <div className="kpi-card">
      <div className="kpi-card__label">{label}</div>
      <div className="kpi-card__value">{formatValue(value, unit)}</div>
      {ci && (
        <div className="kpi-card__ci">
          95% CI [{formatValue(ci.ci95_low, unit)}, {formatValue(ci.ci95_high, unit)}]
        </div>
      )}
    </div>
  );
}
