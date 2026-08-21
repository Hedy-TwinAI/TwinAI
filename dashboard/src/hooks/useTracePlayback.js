import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Drives a `currentIndex` cursor over a fixed-length array via
 * requestAnimationFrame, at `pointsPerSecond` real-time speed. Auto-pauses
 * when the tab is backgrounded (rAF stops firing) and stops at the end.
 *
 * `resetKey` should change identity whenever the underlying data is a new
 * simulation run (e.g. the `results` object) -- `length` alone doesn't
 * change across reruns, since resampleTrace always emits a fixed point count.
 */
export function useTracePlayback(length, pointsPerSecond = 20, resetKey) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const rafRef = useRef(null);
  const lastTsRef = useRef(null);

  useEffect(() => {
    setCurrentIndex(0);
    setIsPlaying(false);
  }, [resetKey]);

  useEffect(() => {
    if (!isPlaying) {
      lastTsRef.current = null;
      return;
    }

    const step = (ts) => {
      if (lastTsRef.current == null) lastTsRef.current = ts;
      const elapsedSec = (ts - lastTsRef.current) / 1000;
      lastTsRef.current = ts;

      setCurrentIndex((prev) => {
        const next = prev + elapsedSec * pointsPerSecond;
        if (next >= length - 1) {
          setIsPlaying(false);
          return length - 1;
        }
        return next;
      });

      rafRef.current = requestAnimationFrame(step);
    };

    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [isPlaying, length, pointsPerSecond]);

  const play = useCallback(() => {
    if (length > 1) setIsPlaying(true);
  }, [length]);
  const pause = useCallback(() => setIsPlaying(false), []);
  const seek = useCallback((index) => {
    setIsPlaying(false);
    setCurrentIndex(Math.max(0, Math.min(length - 1, index)));
  }, [length]);

  return {
    currentIndex: Math.round(currentIndex),
    isPlaying,
    play,
    pause,
    seek,
  };
}
