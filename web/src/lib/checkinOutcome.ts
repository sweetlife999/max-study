import { isApiError } from '../api/errors';
import type { CheckinResult } from '../api/types';

/**
 * §7 does not enumerate error codes of `POST /api/checkins`. These sets hold the codes we expect
 * the backend to use; anything else still gets a sensible screen via HTTP status and the
 * server-provided localized message.
 */
export const INVALID_CODE_ERRORS: ReadonlySet<string> = new Set([
  'invalid_code',
  'code_invalid',
  'code_expired',
  'expired_code',
  'invalid_or_expired_code',
]);

export const CHECKIN_CLOSED_ERRORS: ReadonlySet<string> = new Set([
  'checkin_closed',
  'checkin_not_open',
  'checkin_window_closed',
  'outside_checkin_window',
]);

export type CheckinFailure = 'code_invalid' | 'closed' | 'rate_limited' | 'not_found' | 'other';

export type CheckinOutcome =
  | { kind: 'success'; result: CheckinResult }
  | { kind: 'already'; result: CheckinResult }
  | { kind: 'failure'; failure: CheckinFailure; error: unknown };

export function classifyCheckinError(error: unknown): CheckinFailure {
  if (!isApiError(error)) return 'other';
  if (error.isRateLimited) return 'rate_limited';
  if (INVALID_CODE_ERRORS.has(error.code)) return 'code_invalid';
  if (CHECKIN_CLOSED_ERRORS.has(error.code)) return 'closed';
  if (error.status === 404) return 'not_found';
  return 'other';
}

export function outcomeFromResult(result: CheckinResult): CheckinOutcome {
  return result.already ? { kind: 'already', result } : { kind: 'success', result };
}

export function outcomeFromError(error: unknown): CheckinOutcome {
  return { kind: 'failure', failure: classifyCheckinError(error), error };
}
