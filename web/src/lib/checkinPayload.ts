/**
 * Check-in payloads (docs/ARCHITECTURE.md §5): the QR encodes
 * `https://max.ru/{BOT_USERNAME}?startapp=ci_{event_id}_{code}`, where `code` is exactly six
 * digits. MAX limits `startapp` to 512 characters of `[A-Za-z0-9_-]`.
 */

export interface CheckinPayload {
  eventId: number;
  code: string;
}

export const MAX_START_PARAM_LENGTH = 512;
export const CHECKIN_CODE_LENGTH = 6;

// Positive integer without leading zeros; ASCII digits only.
const PAYLOAD_PATTERN = /^ci_([1-9][0-9]*)_([0-9]{6})$/;
const CODE_PATTERN = /^[0-9]{6}$/;
const MAX_HOST = 'max.ru';

/** Parses a `start_param` value. Returns null for anything but a well-formed check-in payload. */
export function parseCheckinStartParam(value: string | null | undefined): CheckinPayload | null {
  if (typeof value !== 'string' || value.length > MAX_START_PARAM_LENGTH) return null;
  const match = PAYLOAD_PATTERN.exec(value);
  if (!match?.[1] || !match[2]) return null;
  const eventId = Number(match[1]);
  if (!Number.isSafeInteger(eventId)) return null;
  return { eventId, code: match[2] };
}

function isMaxHost(hostname: string): boolean {
  const host = hostname.toLowerCase();
  return host === MAX_HOST || host.endsWith(`.${MAX_HOST}`);
}

function parseUrl(text: string): URL | null {
  const candidate = /^[a-z][a-z0-9+.-]*:/i.test(text) ? text : `https://${text}`;
  try {
    return new URL(candidate);
  } catch {
    return null;
  }
}

/**
 * Extracts a check-in payload from text recognised by the QR scanner: either a MAX deeplink
 * (https only, host max.ru or its subdomain) or a bare `ci_<event>_<code>` payload.
 */
export function extractCheckinFromScan(text: string): CheckinPayload | null {
  const trimmed = text.trim();
  if (trimmed.length === 0) return null;

  const direct = parseCheckinStartParam(trimmed);
  if (direct) return direct;

  const url = parseUrl(trimmed);
  if (!url || url.protocol !== 'https:' || !isMaxHost(url.hostname)) return null;
  return parseCheckinStartParam(url.searchParams.get('startapp'));
}

/** Accepts "123456", "123 456" or "123-456"; returns the six digits or null. */
export function normalizeManualCode(input: string): string | null {
  const compact = input.replace(/[\s-]/g, '');
  return CODE_PATTERN.test(compact) ? compact : null;
}
