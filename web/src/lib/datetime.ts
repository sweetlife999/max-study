/**
 * Date helpers. The API speaks UTC ISO strings (§4); everything the user sees or types is in
 * the university time zone from `/api/me.university.timezone`, regardless of the device zone.
 */
import type { Lang } from '../api/types';

const LOCALES: Record<Lang, string> = { ru: 'ru-RU', en: 'en-GB' };

export function isValidTimeZone(timeZone: string): boolean {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone });
    return true;
  } catch {
    return false;
  }
}

function resolveTimeZone(timeZone: string): string {
  return isValidTimeZone(timeZone) ? timeZone : 'UTC';
}

function formatter(lang: Lang, timeZone: string, options: Intl.DateTimeFormatOptions) {
  return new Intl.DateTimeFormat(LOCALES[lang], {
    ...options,
    timeZone: resolveTimeZone(timeZone),
  });
}

const DATE_TIME: Intl.DateTimeFormatOptions = {
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
};
const TIME: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit' };

export function formatDateTime(iso: string, timeZone: string, lang: Lang): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return formatter(lang, timeZone, DATE_TIME).format(date);
}

export function formatTime(iso: string, timeZone: string, lang: Lang): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return formatter(lang, timeZone, TIME).format(date);
}

/** "18 Sept, 14:00–15:30", or both full date-times if the event spans several days. */
export function formatRange(startIso: string, endIso: string, timeZone: string, lang: Lang) {
  const start = formatDateTime(startIso, timeZone, lang);
  const end = zonedWallTime(endIso, timeZone);
  const startWall = zonedWallTime(startIso, timeZone);
  if (!start || !end || !startWall) return start;
  const sameDay = startWall.slice(0, 10) === end.slice(0, 10);
  const endText = sameDay
    ? formatTime(endIso, timeZone, lang)
    : formatDateTime(endIso, timeZone, lang);
  return `${start} – ${endText}`;
}

interface WallParts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
}

function wallParts(epochMs: number, timeZone: string): WallParts {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: resolveTimeZone(timeZone),
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).formatToParts(new Date(epochMs));
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((part) => part.type === type)?.value ?? Number.NaN);
  return {
    year: get('year'),
    month: get('month'),
    day: get('day'),
    hour: get('hour'),
    minute: get('minute'),
  };
}

const pad = (value: number, length = 2) => String(value).padStart(length, '0');

/** UTC ISO → `YYYY-MM-DDTHH:mm` wall time in `timeZone` (the `datetime-local` input format). */
export function zonedWallTime(iso: string, timeZone: string): string {
  const epoch = Date.parse(iso);
  if (Number.isNaN(epoch)) return '';
  const p = wallParts(epoch, timeZone);
  return `${pad(p.year, 4)}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}`;
}

/** Offset of `timeZone` from UTC at the instant `epochMs`, in milliseconds (minute precision). */
function zoneOffset(epochMs: number, timeZone: string): number {
  const p = wallParts(epochMs, timeZone);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute);
  return asUtc - Math.floor(epochMs / 60_000) * 60_000;
}

const WALL_TIME_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/;

/**
 * `YYYY-MM-DDTHH:mm` typed in `timeZone` → UTC ISO string, or null if the value is not a real
 * calendar date-time. A wall time skipped by a DST jump resolves to the instant after the jump.
 */
export function wallTimeToUtcIso(value: string, timeZone: string): string | null {
  const match = WALL_TIME_PATTERN.exec(value);
  if (!match) return null;
  const [year, month, day, hour, minute] = match.slice(1).map(Number) as [
    number,
    number,
    number,
    number,
    number,
  ];
  const guess = Date.UTC(year, month - 1, day, hour, minute);
  const check = new Date(guess);
  if (
    check.getUTCFullYear() !== year ||
    check.getUTCMonth() !== month - 1 ||
    check.getUTCDate() !== day ||
    hour > 23 ||
    minute > 59
  ) {
    return null;
  }
  const firstOffset = zoneOffset(guess, timeZone);
  let result = guess - firstOffset;
  const secondOffset = zoneOffset(result, timeZone);
  if (secondOffset !== firstOffset) result = guess - secondOffset;
  return new Date(result).toISOString();
}
