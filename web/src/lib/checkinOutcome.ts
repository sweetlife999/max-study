import { isApiError } from '../api/errors';
import type { CheckinResult } from '../api/types';

/** Closed API error list from docs/ARCHITECTURE.md §7. */
export const INVALID_CODE_ERRORS: ReadonlySet<string> = new Set(['invalid_code', 'code_expired']);

export const CHECKIN_CLOSED_ERRORS: ReadonlySet<string> = new Set([
  'checkin_closed',
  'checkin_not_started',
  'checkin_window_over',
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
