export default function PlaybackControls({
  isPlaying,
  onPlay,
  onPause,
  currentIndex,
  length,
  onSeek,
  currentPoint,
}) {
  if (!length) return null;

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={isPlaying ? onPause : onPlay}
          className="rounded-md bg-[var(--series-queue)] px-4 py-1.5 text-sm font-medium text-white"
        >
          {isPlaying ? "Pause" : "Play"}
        </button>
        <input
          type="range"
          min={0}
          max={length - 1}
          step={1}
          value={currentIndex}
          onChange={(e) => onSeek(Number(e.target.value))}
          className="flex-1 accent-[var(--series-queue)]"
        />
        {currentPoint && (
          <div className="whitespace-nowrap text-xs text-[var(--text-secondary)]">
            t={currentPoint.t.toFixed(1)}m · queue {currentPoint.queue_len} · wip{" "}
            {currentPoint.wip} · busy{" "}
            {currentPoint.busy.filter(Boolean).length}/{currentPoint.busy.length}
          </div>
        )}
      </div>
    </div>
  );
}
