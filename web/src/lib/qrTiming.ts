/**
 * When to re-request the rotating QR (§7 `GET /api/org/events/{id}/qr`).
 *
 * The code must be replaced right when the server window ends, but the device clock may be off.
 * We therefore translate the server `expires_at` into local time using an estimate of the clock
 * offset taken at the moment the response arrived:
 *
 * 1. If the HTTP `Date` header is readable, it is the server clock truncated to whole seconds,
 *    i.e. a *lower bound* on the server time. Using the bound as-is biases the refresh late
 *    rather than early: a code fetched too early belongs to the window we already show, while
 *    one fetched just after expiry is rejected by the backend and replaced by this refresh.
 *    The bound is clamped into `[window_started_at, expires_at)`, which must contain server time.
 * 2. Otherwise, if the local clock already falls inside the window, it is trusted (offset 0).
 * 3. Otherwise the local clock is off; assume the server was at the window start. Refetches are
 *    scheduled at window boundaries, so after the first request this is accurate.
 *
 * The delay never exceeds one step, so a bad estimate costs at most one window. If a refetch
 * still returns the same window (we were early), the caller retries after a short pause.
 */

export const QR_REFRESH_GUARD_MS = 150;
export const QR_MIN_DELAY_MS = 250;
export const QR_SAME_WINDOW_RETRY_MS = 500;
export const QR_ERROR_RETRY_MS = 3000;

export interface QrTimingInput {
  windowStartedAt: string;
  expiresAt: string;
  stepSeconds: number;
  /** Local `Date.now()` when the response arrived. */
  receivedAt: number;
  /** Server time from the `Date` header, if any. */
  serverDate: number | null;
}

/** Estimated `local clock - server clock`, in milliseconds. */
export function estimateClockOffset(input: QrTimingInput): number {
  const windowStart = Date.parse(input.windowStartedAt);
  const expires = Date.parse(input.expiresAt);
  if (!Number.isFinite(windowStart) || !Number.isFinite(expires) || expires <= windowStart) {
    return 0;
  }
  if (input.serverDate !== null && Number.isFinite(input.serverDate)) {
    // Lower bound on the server time: never refresh before the server window has really ended.
    const serverNow = Math.min(Math.max(input.serverDate, windowStart), expires - 1);
    return input.receivedAt - serverNow;
  }
  if (input.receivedAt >= windowStart && input.receivedAt < expires) return 0;
  return input.receivedAt - windowStart;
}

/** Milliseconds from `now` (local clock) until the QR should be re-requested. */
export function computeQrRefreshDelay(input: QrTimingInput, now: number): number {
  const windowStart = Date.parse(input.windowStartedAt);
  const expires = Date.parse(input.expiresAt);
  const stepFromBody = input.stepSeconds > 0 ? input.stepSeconds * 1000 : 0;
  const stepFromWindow =
    Number.isFinite(windowStart) && Number.isFinite(expires) ? expires - windowStart : 0;
  const stepMs = Math.max(stepFromBody, stepFromWindow);
  if (!Number.isFinite(expires) || stepMs <= 0) return QR_ERROR_RETRY_MS;

  const refreshAtLocal = expires + estimateClockOffset(input) + QR_REFRESH_GUARD_MS;
  const delay = refreshAtLocal - now;
  return Math.min(Math.max(delay, QR_MIN_DELAY_MS), stepMs + QR_REFRESH_GUARD_MS);
}

/** Whole seconds left until the window ends, as shown in the countdown. */
export function secondsLeft(input: QrTimingInput, now: number): number {
  const expires = Date.parse(input.expiresAt);
  if (!Number.isFinite(expires)) return 0;
  const remaining = expires + estimateClockOffset(input) - now;
  return Math.max(0, Math.ceil(remaining / 1000));
}
