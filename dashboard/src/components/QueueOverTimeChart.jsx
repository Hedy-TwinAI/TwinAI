import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const SERIES = [
  { key: "queue_len", label: "Queue length", color: "var(--series-queue)" },
  { key: "wip", label: "Work in progress", color: "var(--series-wip)" },
];

function renderLegend() {
  return (
    <div className="flex gap-4 pb-2 text-xs text-[var(--text-secondary)]">
      {SERIES.map((s) => (
        <span key={s.key} className="flex items-center gap-1.5">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: s.color }}
          />
          {s.label}
        </span>
      ))}
    </div>
  );
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-[var(--border)] bg-[var(--surface-raised)] px-3 py-2 text-xs text-[var(--text-secondary)]">
      <div className="mb-1 text-[var(--text-muted)]">t = {label?.toFixed(1)} min</div>
      {payload.map((p) => (
        <div key={p.dataKey} className="text-[var(--text-primary)]">
          {SERIES.find((s) => s.key === p.dataKey)?.label}: {p.value}
        </div>
      ))}
    </div>
  );
}

export default function QueueOverTimeChart({ data, playheadT }) {
  if (!data || data.length === 0) return null;
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
      <h3 className="mb-1 text-sm font-medium text-[var(--text-primary)]">
        Queue length &amp; WIP over time
      </h3>
      {renderLegend()}
      <ResponsiveContainer width="100%" height={260}>
        <AreaChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="var(--gridline)" vertical={false} />
          <XAxis
            dataKey="t"
            tickFormatter={(v) => v.toFixed(0)}
            stroke="var(--baseline)"
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            label={{ value: "minutes", position: "insideBottomRight", fill: "var(--text-muted)", fontSize: 11 }}
          />
          <YAxis
            stroke="var(--baseline)"
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            allowDecimals={false}
          />
          <Tooltip content={<ChartTooltip />} />
          {SERIES.map((s) => (
            <Area
              key={s.key}
              type="stepAfter"
              dataKey={s.key}
              stroke={s.color}
              strokeWidth={2}
              fill={s.color}
              fillOpacity={0.1}
              isAnimationActive={false}
            />
          ))}
          {playheadT != null && (
            <ReferenceLine x={playheadT} stroke="var(--text-primary)" strokeWidth={1} />
          )}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
