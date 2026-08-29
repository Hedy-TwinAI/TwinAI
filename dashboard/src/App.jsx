import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./App.css";
import { runSimulation } from "./api";
import { resampleTrace } from "./lib/resampleTrace";
import { useTracePlayback } from "./hooks/useTracePlayback";
import KpiGrid from "./components/KpiGrid";
import ControlPanel from "./components/ControlPanel";
import QueueOverTimeChart from "./components/QueueOverTimeChart";
import UtilizationByResourceChart from "./components/UtilizationByResourceChart";
import PlaybackControls from "./components/PlaybackControls";
import BrewLineScene from "./components/BrewLineScene";
import AssistantChat from "./components/AssistantChat";

// reps/seed are fixed -- horizon and the other checklist knobs are exposed.
const FIXED = { reps: 50, seed: 42 };
const DEFAULT_KNOBS = { arrival_rate: 0.5, num_baristas: 2, mean_service_time: 3.0, horizon: 480 };
// Stable reference so `results?.trace ?? EMPTY_TRACE` doesn't create a new
// array identity on every render while `results` is still null -- a fresh
// `[]` literal here would spuriously retrigger effects keyed on `trace`.
const EMPTY_TRACE = [];

// The sidebar is only a side-by-side panel at this breakpoint (matches the
// `.app__body`/`.app__sidebar` rules in App.css) -- below it, it stacks
// under `main` at a fixed height instead, so width-dragging doesn't apply.
const DESKTOP_QUERY = "(min-width: 1024px)";
const DEFAULT_SIDEBAR_WIDTH = 380;
const MIN_SIDEBAR_WIDTH = 280;
const MAX_SIDEBAR_WIDTH_RATIO = 0.6;

export default function App() {
  const [knobs, setKnobs] = useState(DEFAULT_KNOBS);
  const [results, setResults] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const requestIdRef = useRef(0);

  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(DEFAULT_SIDEBAR_WIDTH);
  const [isResizing, setIsResizing] = useState(false);
  const [isDesktop, setIsDesktop] = useState(() => window.matchMedia(DESKTOP_QUERY).matches);
  const isDraggingRef = useRef(false);

  useEffect(() => {
    const mql = window.matchMedia(DESKTOP_QUERY);
    const handleChange = (e) => setIsDesktop(e.matches);
    mql.addEventListener("change", handleChange);
    return () => mql.removeEventListener("change", handleChange);
  }, []);

  const handleResizeStart = useCallback((e) => {
    e.preventDefault();
    isDraggingRef.current = true;
    setIsResizing(true);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  useEffect(() => {
    function handleMouseMove(e) {
      if (!isDraggingRef.current) return;
      const maxWidth = window.innerWidth * MAX_SIDEBAR_WIDTH_RATIO;
      const nextWidth = Math.min(Math.max(window.innerWidth - e.clientX, MIN_SIDEBAR_WIDTH), maxWidth);
      setSidebarWidth(nextWidth);
    }
    function handleMouseUp() {
      if (!isDraggingRef.current) return;
      isDraggingRef.current = false;
      setIsResizing(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    }
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, []);

  const runCurrentKnobs = useCallback(async (targetKnobs) => {
    setIsLoading(true);
    setError(null);
    const requestId = ++requestIdRef.current;
    try {
      const data = await runSimulation({ ...targetKnobs, ...FIXED });
      if (requestId !== requestIdRef.current) return; // a newer run superseded this one
      setResults(data);
    } catch (err) {
      if (requestId !== requestIdRef.current) return;
      setError(err.message);
    } finally {
      if (requestId === requestIdRef.current) setIsLoading(false);
    }
  }, []);

  // Run once on load so the dashboard isn't empty; after that, inputs only
  // recompute when the user clicks "Run simulation".
  useEffect(() => {
    runCurrentKnobs(DEFAULT_KNOBS);
  }, [runCurrentKnobs]);

  const resampled = useMemo(() => resampleTrace(results?.trace ?? EMPTY_TRACE, 300), [results]);
  const playback = useTracePlayback(resampled.length, 20, results);
  const currentPoint = resampled[playback.currentIndex];
  const dt = resampled.length > 1 ? resampled[1].t - resampled[0].t : 0;
  const continuousSimTime = playback.rawIndex * dt;
  const maxConcurrency = Math.ceil(
    Math.max(results?.summary?.max_wip?.max ?? 0, results?.summary?.max_queue_length?.max ?? 0),
  );

  return (
    <div className="app">
      <header className="app__header">
        <div className="app__header-text">
          <h1 className="app__title">BrewLine</h1>
          <p className="app__subtitle">Coffee shop digital twin — live SimPy KPI dashboard</p>
        </div>
        <button
          type="button"
          className="app__sidebar-toggle"
          onClick={() => setIsSidebarOpen((open) => !open)}
        >
          {isSidebarOpen ? "Hide chat" : "Show chat"}
        </button>
      </header>

      <div className="app__body">
        <main className="app__main">
          <div className="app__main__section">
            <div className="app__section">
              <ControlPanel
                knobs={knobs}
                onChange={setKnobs}
                onRun={() => runCurrentKnobs(knobs)}
                isLoading={isLoading}
                error={error}
              />
            </div>

            <div className="app__section">
              <KpiGrid summary={results?.summary} />
            </div>

            <div className="app__section">
              <BrewLineScene
                trace={results?.trace ?? EMPTY_TRACE}
                currentTime={continuousSimTime}
                numBaristas={knobs.num_baristas}
                resetKey={results}
                maxConcurrency={maxConcurrency}
              />
            </div>

            <div className="app__section app__charts">
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
        </main>

        {isSidebarOpen && (
          <>
            {isDesktop && (
              <div
                className={`app__resize-handle${isResizing ? " app__resize-handle--active" : ""}`}
                onMouseDown={handleResizeStart}
              />
            )}
            <aside
              className="app__sidebar"
              style={isDesktop ? { width: sidebarWidth } : undefined}
            >
              <AssistantChat context={results} />
            </aside>
          </>
        )}
      </div>
    </div>
  );
}
