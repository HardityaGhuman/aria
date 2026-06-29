import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Smooths streamed text. Tokens arrive in bursts (snappy); this reveals the
 * target string a few characters per animation frame so it reads like typing.
 *
 *   push(delta)     append newly streamed text to the target
 *   finalize(full)  set the authoritative final text (from the done envelope)
 *   reset()         clear for a new message
 *   shown           the progressively revealed text to render
 */
export function useSmoothText() {
  const [shown, setShown] = useState("");
  const target = useRef("");
  const shownLen = useRef(0);
  const raf = useRef<number | null>(null);

  const tick = useCallback(() => {
    const remaining = target.current.length - shownLen.current;
    if (remaining <= 0) {
      raf.current = null;
      return;
    }
    // Reveal proportionally, but gently: ~10% of the backlog per frame, at least
    // 1 char and capped at 4 so even a large burst types out calmly (easier on
    // the eye) instead of dumping. ~60fps → up to ~240 chars/s, easing to ~60/s.
    const step = Math.min(4, Math.max(1, Math.ceil(remaining * 0.1)));
    shownLen.current = Math.min(target.current.length, shownLen.current + step);
    setShown(target.current.slice(0, shownLen.current));
    raf.current = requestAnimationFrame(tick);
  }, []);

  const ensureRunning = useCallback(() => {
    if (raf.current == null) raf.current = requestAnimationFrame(tick);
  }, [tick]);

  const push = useCallback(
    (delta: string) => {
      target.current += delta;
      ensureRunning();
    },
    [ensureRunning],
  );

  const finalize = useCallback(
    (full: string) => {
      target.current = full;
      ensureRunning();
    },
    [ensureRunning],
  );

  const reset = useCallback(() => {
    if (raf.current != null) cancelAnimationFrame(raf.current);
    raf.current = null;
    target.current = "";
    shownLen.current = 0;
    setShown("");
  }, []);

  useEffect(() => {
    return () => {
      if (raf.current != null) cancelAnimationFrame(raf.current);
    };
  }, []);

  return { shown, push, finalize, reset };
}
