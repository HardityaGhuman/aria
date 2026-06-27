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
    // Reveal proportionally so big backlogs catch up fast, small ones stay smooth.
    const step = Math.max(2, Math.ceil(remaining * 0.18));
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
