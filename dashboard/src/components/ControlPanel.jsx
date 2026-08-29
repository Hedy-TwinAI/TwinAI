import "./ControlPanel.css";

const FIELDS = [
  { key: "arrival_rate", label: "Arrival rate", unit: "customers/min", min: 0.05, max: 5, step: 0.05 },
  { key: "num_baristas", label: "Baristas", unit: "servers", min: 1, max: 10, step: 1 },
  { key: "mean_service_time", label: "Mean service time", unit: "min", min: 0.5, max: 20, step: 0.5 },
  { key: "horizon", label: "Horizon", unit: "min", min: 60, max: 1440, step: 30 },
];

export default function ControlPanel({ knobs, onChange, onRun, isLoading, error }) {
  return (
    <div className="control-panel">
      <div className="control-panel__header">
        <h3 className="control-panel__title">Simulation inputs</h3>
        <div className="control-panel__actions">
          {isLoading && <span className="control-panel__status">running…</span>}
          <button
            type="button"
            onClick={onRun}
            disabled={isLoading}
            className="control-panel__run-btn"
          >
            Run simulation
          </button>
        </div>
      </div>
      <div className="control-panel__fields">
        {FIELDS.map(({ key, label, unit, min, max, step }) => (
          <label key={key} className="control-panel__field">
            <div className="control-panel__field-header">
              <span>{label}</span>
              <span className="control-panel__field-value">
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
              className="control-panel__range"
            />
          </label>
        ))}
      </div>
      {error && <div className="control-panel__error">{error}</div>}
    </div>
  );
}
