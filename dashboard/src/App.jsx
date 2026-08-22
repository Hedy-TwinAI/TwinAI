import { useEffect, useMemo, useRef, useState } from "react";
import { runSimulation } from "./api";
import { resampleTrace } from "./lib/resampleTrace";
import { useTracePlayback } from "./hooks/useTracePlayback";
import KpiGrid from "./components/KpiGrid";
import ControlPanel from "./components/ControlPanel";
import QueueOverTimeChart from "./components/QueueOverTimeChart";
import UtilizationByResourceChart from "./components/UtilizationByResourceChart";
import PlaybackControls from "./components/PlaybackControls";
import BrewLineScene from "./components/BrewLineScene";

// reps/seed are fixed -- horizon and the other checklist knobs are exposed.
const FIXED = { reps: 50, seed: 42 };
const DEFAULT_KNOBS = { arrival_rate: 0.5, num_baristas: 2, mean_service_time: 3.0, horizon: 480 };
// Stable reference so `results?.trace ?? EMPTY_TRACE` doesn't create a new
// array identity on every render while `results` is still null -- a fresh
// `[]` literal here would spuriously retrigger effects keyed on `trace`.
const EMPTY_TRACE = [];

export default function App() {
  const [knobs, setKnobs] = useState(DEFAULT_KNOBS);
  const [results, setResults] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const debounceRef = useRef(null);
  const requestIdRef = useRef(0);

  useEffect(() => {
    setIsLoading(true);
    setError(null);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      const requestId = ++requestIdRef.current;
      try {
        const data = await runSimulation({ ...knobs, ...FIXED });
        if (requestId !== requestIdRef.current) return; // a newer knob change superseded this one
        setResults(data);
      } catch (err) {
        if (requestId !== requestIdRef.current) return;
        setError(err.message);
      } finally {
        if (requestId === requestIdRef.current) setIsLoading(false);
      }
    }, 400);
    return () => clearTimeout(debounceRef.current);
  }, [knobs]);

  const resampled = useMemo(() => resampleTrace(results?.trace ?? EMPTY_TRACE, 300), [results]);
  const playback = useTracePlayback(resampled.length, 20, results);
  const currentPoint = resampled[playback.currentIndex];
  const dt = resampled.length > 1 ? resampled[1].t - resampled[0].t : 0;
  const continuousSimTime = playback.rawIndex * dt;
  const maxConcurrency = Math.ceil(
    Math.max(results?.summary?.max_wip?.max ?? 0, results?.summary?.max_queue_length?.max ?? 0),
  );

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-[var(--text-primary)]">BrewLine</h1>
        <p className="text-sm text-[var(--text-secondary)]">
          Coffee shop digital twin — live SimPy KPI dashboard
        </p>
      </header>

      <div className="mb-6">
        <ControlPanel knobs={knobs} onChange={setKnobs} isLoading={isLoading} error={error} />
      </div>

      <div className="mb-6">
        <KpiGrid summary={results?.summary} />
      </div>

      <div className="mb-6">
        <BrewLineScene
          trace={results?.trace ?? EMPTY_TRACE}
          currentTime={continuousSimTime}
          numBaristas={knobs.num_baristas}
          resetKey={results}
          maxConcurrency={maxConcurrency}
        />
      </div>

      <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <QueueOverTimeChart data={resampled} playheadT={currentPoint?.t} />
        <UtilizationByResourceChart resourceKpis={results?.resource_kpis} />
      </div>

      <PlaybackControls
        isPlaying={playback.isPlaying}
        onPlay={playback.play}
        onPause={playback.pause}
        currentIndex={playback.currentIndex}
        length={resampled.length}
        onSeek={playback.seek}
        currentPoint={currentPoint}
      />
    </div>
  );
}
