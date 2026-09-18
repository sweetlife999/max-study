import { useCallback, useEffect, useRef, useState } from 'react';

import {
  computeQrRefreshDelay,
  QR_ERROR_RETRY_MS,
  QR_SAME_WINDOW_RETRY_MS,
  type QrTimingInput,
} from '../lib/qrTiming';
import { useApi } from './context';
import { isApiError } from './errors';
import type { EventQr } from './types';

export interface RotatingQr {
  qr: EventQr | null;
  timing: QrTimingInput | null;
  /** Error of the most recent request; null while refreshes succeed. */
  error: unknown;
  /** True until the first successful response. */
  isLoading: boolean;
  /** Fetches immediately, cancelling the scheduled refresh. */
  refresh: () => void;
}

interface Snapshot {
  qr: EventQr | null;
  timing: QrTimingInput | null;
  error: unknown;
}

const INITIAL: Snapshot = { qr: null, timing: null, error: null };

/**
 * Keeps `GET /api/org/events/{id}/qr` (§7) fresh, re-requesting exactly when the server window
 * ends. The delay is computed from `window_started_at`/`expires_at` and the moment the response
 * arrived, so a device clock that is off does not shorten or stretch the window (see qrTiming).
 *
 * A failed refresh keeps the previous code on screen and is reported through `error` — the
 * countdown reaching zero tells the organizer the code may be stale. Transient failures are
 * retried on their own; a deterministic 4xx (§7 answers `403` as soon as `checkin_open` is
 * false, `401` once `initData` expires, `404` for a deleted event) would answer the same way
 * forever, so the loop stops and only the explicit `refresh` restarts it.
 */
export function useRotatingQr(eventId: number, enabled: boolean): RotatingQr {
  const api = useApi();
  const [snapshot, setSnapshot] = useState<Snapshot>(INITIAL);
  const [attempt, setAttempt] = useState(0);

  const refresh = useCallback(() => setAttempt((value) => value + 1), []);

  // `attempt` restarts the loop; the ref keeps the last window across those restarts.
  const lastWindowRef = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled) {
      setSnapshot(INITIAL);
      lastWindowRef.current = null;
      return undefined;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const schedule = (delay: number) => {
      timer = setTimeout(() => void tick(), delay);
    };

    const tick = async (): Promise<void> => {
      try {
        const { qr, receivedAt, serverDate } = await api.getEventQr(eventId);
        if (cancelled) return;
        const timing: QrTimingInput = {
          windowStartedAt: qr.window_started_at,
          expiresAt: qr.expires_at,
          stepSeconds: qr.step_seconds,
          receivedAt,
          serverDate,
        };
        // The server may still be inside the previous window if our clock ran ahead.
        const sameWindow = lastWindowRef.current === qr.window_started_at;
        lastWindowRef.current = qr.window_started_at;
        setSnapshot({ qr, timing, error: null });
        schedule(sameWindow ? QR_SAME_WINDOW_RETRY_MS : computeQrRefreshDelay(timing, Date.now()));
      } catch (error) {
        if (cancelled) return;
        setSnapshot((current) => ({ ...current, error }));
        // Retrying a deterministic 4xx every few seconds only hammers the API and never
        // recovers; the screen keeps the last code, warns, and offers `refresh`.
        if (!(isApiError(error) && error.isClientError)) schedule(QR_ERROR_RETRY_MS);
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [api, eventId, enabled, attempt]);

  return {
    qr: snapshot.qr,
    timing: snapshot.timing,
    error: snapshot.error,
    isLoading: snapshot.qr === null && snapshot.error === null,
    refresh,
  };
}
