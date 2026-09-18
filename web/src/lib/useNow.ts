import { useEffect, useState } from 'react';

/** Re-renders on a fixed interval so countdowns stay in step with the wall clock. */
export function useNow(intervalMs: number, enabled = true): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!enabled) return undefined;
    // A zero timeout, not a direct call: setting state inside an effect body cascades renders.
    const first = setTimeout(() => setNow(Date.now()), 0);
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => {
      clearTimeout(first);
      clearInterval(id);
    };
  }, [intervalMs, enabled]);

  return now;
}
