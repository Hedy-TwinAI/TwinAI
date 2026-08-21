import KpiCard from "./KpiCard";

const CARDS = [
  { key: "utilization", label: "Barista utilization", unit: "pct" },
  { key: "throughput", label: "Throughput", unit: "per_min" },
  { key: "cmax", label: "Cmax (last departure)", unit: "min" },
  { key: "avg_wait", label: "Avg wait", unit: "min" },
];

export default function KpiGrid({ summary }) {
  if (!summary) return null;
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      {CARDS.map(({ key, label, unit }) => (
        <KpiCard key={key} label={label} value={summary[key]?.mean} unit={unit} ci={summary[key]} />
      ))}
    </div>
  );
}
