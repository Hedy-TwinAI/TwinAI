import "./PlaybackControls.css";

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
    <div className="playback-controls">
      <div className="playback-controls__row">
        <button type="button" onClick={isPlaying ? onPause : onPlay} className="playback-controls__play-btn">
          {isPlaying ? "Pause" : "Play"}
        </button>
        <input
          type="range"
          min={0}
          max={length - 1}
          step={1}
          value={currentIndex}
          onChange={(e) => onSeek(Number(e.target.value))}
          className="playback-controls__range"
        />
        {currentPoint && (
          <div className="playback-controls__info">
            t={currentPoint.t.toFixed(1)}m · queue {currentPoint.queue_len} · wip{" "}
            {currentPoint.wip} · busy{" "}
            {currentPoint.busy.filter(Boolean).length}/{currentPoint.busy.length}
          </div>
        )}
      </div>
    </div>
  );
}
