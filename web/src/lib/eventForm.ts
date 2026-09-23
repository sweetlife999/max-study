import type { CreateEventRequest, Event } from '../api/types';
import { wallTimeToUtcIso, zonedWallTime } from './datetime';

/** Raw form state: every field is a string, exactly as the DOM controls hold it. */
export interface EventForm {
  title: string;
  description: string;
  kind: string;
  location: string;
  /** `YYYY-MM-DDTHH:mm` wall time in the university time zone. */
  startsAt: string;
  endsAt: string;
  /** Empty means "let the backend apply the default points of this kind" (§7). */
  points: string;
  /** Empty means "not linked to an onboarding step". */
  onboardingStep: string;
}

export interface WallTimeParts {
  date: string;
  time: string;
}

export function splitWallTime(value: string): WallTimeParts {
  const [date = '', time = ''] = value.split('T');
  return { date, time };
}

export function combineWallTime(date: string, time: string): string {
  if (date === '' && time === '') return '';
  return `${date}T${time}`;
}

export type FieldError = 'required' | 'invalid' | 'before_start';

export interface EventFormErrors {
  title?: FieldError;
  kind?: FieldError;
  startsAt?: FieldError;
  endsAt?: FieldError;
  points?: FieldError;
}

const HOUR_MS = 60 * 60 * 1000;
const POINTS_PATTERN = /^\d{1,6}$/;

export function emptyEventForm(timezone: string, now: number = Date.now()): EventForm {
  const start = Math.ceil(now / HOUR_MS) * HOUR_MS;
  return {
    title: '',
    description: '',
    kind: '',
    location: '',
    startsAt: zonedWallTime(new Date(start).toISOString(), timezone),
    endsAt: zonedWallTime(new Date(start + HOUR_MS).toISOString(), timezone),
    points: '',
    onboardingStep: '',
  };
}

export function eventToForm(event: Event, timezone: string): EventForm {
  return {
    title: event.title,
    description: event.description,
    kind: event.kind,
    location: event.location,
    startsAt: zonedWallTime(event.starts_at, timezone),
    endsAt: zonedWallTime(event.ends_at, timezone),
    points: String(event.points),
    onboardingStep: event.onboarding_step ?? '',
  };
}

export interface EventFormValidation {
  errors: EventFormErrors;
  /** The request body, or null when at least one field is invalid. */
  request: CreateEventRequest | null;
}

/**
 * Validates the organizer form against §7 (`POST /api/org/events`) and §4 (`ends_at > starts_at`).
 * Times are typed in the university time zone and sent as UTC ISO strings.
 */
export function validateEventForm(form: EventForm, timezone: string): EventFormValidation {
  const errors: EventFormErrors = {};

  const title = form.title.trim();
  if (title === '') errors.title = 'required';
  if (form.kind === '') errors.kind = 'required';

  const startsAt = wallTimeToUtcIso(form.startsAt, timezone);
  if (startsAt === null) errors.startsAt = 'invalid';

  const endsAt = wallTimeToUtcIso(form.endsAt, timezone);
  if (endsAt === null) {
    errors.endsAt = 'invalid';
  } else if (startsAt !== null && Date.parse(endsAt) <= Date.parse(startsAt)) {
    errors.endsAt = 'before_start';
  }

  const rawPoints = form.points.trim();
  const hasPoints = rawPoints !== '';
  if (hasPoints && !POINTS_PATTERN.test(rawPoints)) errors.points = 'invalid';

  if (Object.keys(errors).length > 0 || startsAt === null || endsAt === null) {
    return { errors, request: null };
  }

  return {
    errors,
    request: {
      title,
      description: form.description.trim(),
      kind: form.kind,
      location: form.location.trim(),
      starts_at: startsAt,
      ends_at: endsAt,
      ...(hasPoints ? { points: Number(rawPoints) } : {}),
      onboarding_step: form.onboardingStep === '' ? null : form.onboardingStep,
    },
  };
}
