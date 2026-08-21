import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

function ChartTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const { name, mean, ci95_low, ci95_high } = payload[0].payload;
  return (
    <div className="rounded-md border border-[var(--border)] bg-[var(--surface-raised)] px-3 py-2 text-xs text-[var(--text-secondary)]">
      <div className="text-[var(--text-primary)]">{name}</div>
      <div>utilization: {(mean * 100).toFixed(1)}%</div>
      <div className="text-[var(--text-muted)]">
        95% CI [{(ci95_low * 100).toFixed(1)}%, {(ci95_high * 100).toFixed(1)}%]
      </div>
    </div>
  );
}

export default function UtilizationByResourceChart({ resourceKpis }) {
  if (!resourceKpis) return null;
  const data = resourceKpis.utilization_by_barista.map((stat, i) => ({
    name: `Barista ${i + 1}`,
    ...stat,
  }));

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
      <h3 className="mb-3 text-sm font-medium text-[var(--text-primary)]">
        Utilization by resource
      </h3>
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="var(--gridline)" vertical={false} />
          <XAxis
            dataKey="name"
            stroke="var(--baseline)"
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
          />
          <YAxis
            stroke="var(--baseline)"
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            tickFormatter={(v) => `${Math.round(v * 100)}%`}
            domain={[0, 1]}
          />
          <Tooltip content={<ChartTooltip />} />
          <Bar dataKey="mean" maxBarSize={24} radius={[4, 4, 0, 0]} isAnimationActive={false}>
            {data.map((_, i) => (
              <Cell key={i} fill="var(--series-util)" />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
