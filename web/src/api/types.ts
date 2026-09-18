/**
 * API types, written by hand strictly after docs/ARCHITECTURE.md §7.
 *
 * TODO: replace this single file with types generated from docs/openapi.json once the backend
 * publishes it. Nothing else in the app declares API shapes, so the swap stays local.
 *
 * All timestamps are ISO 8601 strings in UTC (§4).
 */

export type Lang = 'ru' | 'en';

export interface University {
  name: string;
  timezone: string;
}

export interface Me {
  id: number;
  first_name: string;
  lang: Lang;
  consent: boolean;
  is_organizer: boolean;
  is_admin: boolean;
  points: number;
  university: University;
}

export interface UpdateMeRequest {
  lang: Lang;
}

export type OnboardingStepType = 'event_kind' | 'manual';

export interface Step {
  key: string;
  type: OnboardingStepType;
  title: string;
  description: string;
  done: boolean;
  event_kind?: string | null;
}

export interface Onboarding {
  steps: Step[];
  done_count: number;
  total: number;
}

export interface Event {
  id: number;
  title: string;
  description: string;
  kind: string;
  kind_title: string;
  location: string;
  starts_at: string;
  ends_at: string;
  points: number;
  onboarding_step: string | null;
  checkin_open: boolean;
  rsvp: boolean;
  checked_in: boolean;
  attendees_count: number;
}

export interface EventList {
  items: Event[];
}

export type EventScope = 'upcoming' | 'past';

export type CheckinMethod = 'qr' | 'code';

export interface CheckinRequest {
  event_id: number;
  code: string;
  method: CheckinMethod;
}

export interface CheckinResult {
  event: Event;
  already: boolean;
  points_total: number;
  completed_step?: Step | null;
}

export interface CreateEventRequest {
  title: string;
  description: string;
  kind: string;
  location: string;
  starts_at: string;
  ends_at: string;
  points?: number;
  onboarding_step?: string | null;
}

export type UpdateEventRequest = Partial<CreateEventRequest> & { checkin_open?: boolean };

export interface EventQr {
  code: string;
  deeplink: string;
  window_started_at: string;
  expires_at: string;
  step_seconds: number;
}

export interface AttendanceItem {
  user_id: number;
  first_name: string;
  method: CheckinMethod;
  checked_in_at: string;
}

export interface Attendance {
  items: AttendanceItem[];
  rsvp_count: number;
  checkin_count: number;
}

export interface Invite {
  token: string;
  deeplink: string;
  expires_at: string;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
  };
}

export interface AppConfig {
  event_kinds: { key: string; title: string; default_points: number }[];
  onboarding_steps: {
    key: string;
    type: OnboardingStepType;
    title: string;
    event_kind?: string | null;
  }[];
  languages: Lang[];
  university: University;
}
