import { parseCheckinStartParam } from './checkinPayload';

/** Route shown when a `ci_…` start param or QR link cannot be parsed. */
export const INVALID_CHECKIN_ROUTE = '/checkin/invalid';

export const CHECKIN_PAYLOAD_PREFIX = 'ci_';

/**
 * Turns `initDataUnsafe.start_param` into the route the app must open (§9).
 *
 * `ci_<event>_<code>` goes straight to the automatic check-in. A payload that clearly means
 * "check in" but does not parse gets an explaining screen instead of a silent main screen —
 * the user must never be left wondering whether the scan worked.
 * Anything else (including `null`) keeps the main screen.
 */
export function startParamRoute(startParam: string | null | undefined): string | null {
  if (typeof startParam !== 'string' || startParam === '') return null;
  const payload = parseCheckinStartParam(startParam);
  if (payload) return `/checkin/qr/${payload.eventId}/${payload.code}`;
  return startParam.startsWith(CHECKIN_PAYLOAD_PREFIX) ? INVALID_CHECKIN_ROUTE : null;
}
