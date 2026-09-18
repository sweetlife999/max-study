import { http, HttpResponse, type HttpResponseResolver } from 'msw';

import { INIT_DATA_HEADER } from '../api/client';
import type {
  Attendance,
  CheckinRequest,
  CheckinResult,
  CreateEventRequest,
  Event,
  EventQr,
  Invite,
  Lang,
  Me,
  Onboarding,
  Step,
  UpdateEventRequest,
} from '../api/types';
import {
  currentWindow,
  isCodeValid,
  isStepDone,
  isWithinCheckinWindow,
  localize,
  MOCK_BOT_USERNAME,
  MOCK_RATE_LIMIT,
  MOCK_STEP_SECONDS,
  mockCode,
  mockDb,
  type MockEvent,
  type MockStep,
} from './db';

const API = '*/api';

type ErrorCode =
  | 'unauthorized'
  | 'consent_required'
  | 'not_found'
  | 'forbidden'
  | 'validation_error'
  | 'invalid_code'
  | 'checkin_closed'
  | 'rate_limited';

const MESSAGES: Record<ErrorCode, Record<Lang, string>> = {
  unauthorized: { ru: 'Откройте приложение в MAX.', en: 'Open the app in MAX.' },
  consent_required: { ru: 'Нужно согласие.', en: 'Consent is required.' },
  not_found: { ru: 'Не найдено.', en: 'Not found.' },
  forbidden: { ru: 'Недостаточно прав.', en: 'Not allowed.' },
  validation_error: { ru: 'Проверьте поля формы.', en: 'Check the form fields.' },
  invalid_code: { ru: 'Неверный или устаревший код.', en: 'Invalid or expired code.' },
  checkin_closed: { ru: 'Отметка закрыта.', en: 'Check-in is closed.' },
  rate_limited: { ru: 'Слишком много попыток.', en: 'Too many attempts.' },
};

const STATUS: Record<ErrorCode, number> = {
  unauthorized: 401,
  consent_required: 403,
  not_found: 404,
  forbidden: 403,
  validation_error: 422,
  invalid_code: 400,
  checkin_closed: 409,
  rate_limited: 429,
};

export function apiError(code: ErrorCode, lang: Lang = mockDb().me.lang) {
  return HttpResponse.json(
    { error: { code, message: MESSAGES[code][lang] } },
    {
      status: STATUS[code],
      headers: code === 'rate_limited' ? { 'Retry-After': '60' } : undefined,
    },
  );
}

function toIso(ms: number): string {
  return new Date(ms).toISOString();
}

function serializeMe(): Me {
  const db = mockDb();
  const points = db.events
    .filter((event) => db.checkins.has(event.id))
    .reduce((sum, event) => sum + event.points, 0);
  return {
    id: db.me.id,
    first_name: db.me.firstName,
    lang: db.me.lang,
    consent: db.me.consent,
    is_organizer: db.me.isOrganizer,
    is_admin: db.me.isAdmin,
    points,
    university: { name: localize(db.university.name, db.me.lang), timezone: db.university.timezone },
  };
}

function serializeEvent(event: MockEvent): Event {
  const db = mockDb();
  const kind = db.kinds.find((k) => k.key === event.kind);
  return {
    id: event.id,
    title: event.title,
    description: event.description,
    kind: event.kind,
    kind_title: kind ? localize(kind.title, db.me.lang) : event.kind,
    location: event.location,
    starts_at: toIso(event.startsAt),
    ends_at: toIso(event.endsAt),
    points: event.points,
    onboarding_step: event.onboardingStep,
    checkin_open: event.checkinOpen,
    rsvp: db.rsvps.has(event.id),
    checked_in: db.checkins.has(event.id),
    attendees_count: event.others.length + (db.checkins.has(event.id) ? 1 : 0),
  };
}

function serializeStep(step: MockStep): Step {
  const db = mockDb();
  return {
    key: step.key,
    type: step.type,
    title: localize(step.title, db.me.lang),
    description: localize(step.description, db.me.lang),
    done: isStepDone(step, db),
    ...(step.eventKind ? { event_kind: step.eventKind } : {}),
  };
}

/** Wraps a resolver with the auth (§7 header) and consent gate. */
function guarded(resolver: HttpResponseResolver, options: { allowWithoutConsent?: boolean } = {}) {
  const wrapped: HttpResponseResolver = (info) => {
    if (!info.request.headers.get(INIT_DATA_HEADER)) return apiError('unauthorized');
    if (!options.allowWithoutConsent && !mockDb().me.consent) return apiError('consent_required');
    return resolver(info);
  };
  return wrapped;
}

function findEvent(rawId: unknown): MockEvent | undefined {
  const id = Number(rawId);
  return mockDb().events.find((event) => event.id === id);
}

function findOwnEvent(rawId: unknown): MockEvent | 'not_found' | 'forbidden' {
  const db = mockDb();
  if (!db.me.isOrganizer) return 'forbidden';
  const event = findEvent(rawId);
  if (!event) return 'not_found';
  return event.organizerId === db.me.id ? event : 'forbidden';
}

function validateEventFields(fields: Partial<CreateEventRequest>, base?: MockEvent): string | null {
  const db = mockDb();
  const startsAt = fields.starts_at ? Date.parse(fields.starts_at) : base?.startsAt;
  const endsAt = fields.ends_at ? Date.parse(fields.ends_at) : base?.endsAt;
  if (fields.title !== undefined && fields.title.trim() === '') return 'title';
  if (fields.kind !== undefined && !db.kinds.some((k) => k.key === fields.kind)) return 'kind';
  if (startsAt === undefined || endsAt === undefined || Number.isNaN(startsAt)) return 'starts_at';
  if (Number.isNaN(endsAt) || endsAt <= startsAt) return 'ends_at';
  if (fields.points !== undefined && (!Number.isInteger(fields.points) || fields.points < 0)) {
    return 'points';
  }
  return null;
}

function applyEventFields(event: MockEvent, fields: UpdateEventRequest): void {
  if (fields.title !== undefined) event.title = fields.title;
  if (fields.description !== undefined) event.description = fields.description;
  if (fields.kind !== undefined) event.kind = fields.kind;
  if (fields.location !== undefined) event.location = fields.location;
  if (fields.starts_at !== undefined) event.startsAt = Date.parse(fields.starts_at);
  if (fields.ends_at !== undefined) event.endsAt = Date.parse(fields.ends_at);
  if (fields.points !== undefined) event.points = fields.points;
  if (fields.onboarding_step !== undefined) event.onboardingStep = fields.onboarding_step;
  if (fields.checkin_open !== undefined) event.checkinOpen = fields.checkin_open;
}

function checkin(body: CheckinRequest) {
  const db = mockDb();
  const now = Date.now();
  db.attempts = db.attempts.filter((at) => now - at < MOCK_RATE_LIMIT.windowMs);
  if (db.attempts.length >= MOCK_RATE_LIMIT.attempts) return apiError('rate_limited');
  db.attempts.push(now);

  const event = findEvent(body.event_id);
  if (!event) return apiError('not_found');
  if (db.checkins.has(event.id)) {
    const already: CheckinResult = {
      event: serializeEvent(event),
      already: true,
      points_total: serializeMe().points,
    };
    return HttpResponse.json(already);
  }
  if (!event.checkinOpen || !isWithinCheckinWindow(event, now)) return apiError('checkin_closed');
  if (!isCodeValid(event.id, body.code, now)) return apiError('invalid_code');

  const pendingSteps = db.steps.filter((step) => step.type === 'event_kind' && !isStepDone(step, db));
  db.checkins.set(event.id, { method: body.method, at: now });
  const completed = pendingSteps.find((step) => isStepDone(step, db));
  const result: CheckinResult = {
    event: serializeEvent(event),
    already: false,
    points_total: serializeMe().points,
    ...(completed ? { completed_step: serializeStep(completed) } : {}),
  };
  return HttpResponse.json(result);
}

export const handlers = [
  http.get(
    `${API}/me`,
    guarded(() => HttpResponse.json(serializeMe()), { allowWithoutConsent: true }),
  ),
  http.post(
    `${API}/me/consent`,
    guarded(
      () => {
        mockDb().me.consent = true;
        return HttpResponse.json(serializeMe());
      },
      { allowWithoutConsent: true },
    ),
  ),
  http.patch(
    `${API}/me`,
    guarded(
      async ({ request }) => {
        const body = (await request.json()) as { lang?: unknown };
        if (body.lang !== 'ru' && body.lang !== 'en') return apiError('validation_error');
        mockDb().me.lang = body.lang;
        return HttpResponse.json(serializeMe());
      },
      { allowWithoutConsent: true },
    ),
  ),

  http.get(
    `${API}/onboarding`,
    guarded(() => {
      const steps = mockDb().steps.map(serializeStep);
      const body: Onboarding = {
        steps,
        done_count: steps.filter((s) => s.done).length,
        total: steps.length,
      };
      return HttpResponse.json(body);
    }),
  ),
  http.post(
    `${API}/onboarding/:key/complete`,
    guarded(({ params }) => {
      const step = mockDb().steps.find((s) => s.key === params.key);
      if (!step) return apiError('not_found');
      if (step.type !== 'manual') return apiError('forbidden');
      mockDb().manualDone.add(step.key);
      return HttpResponse.json(serializeStep(step));
    }),
  ),

  http.get(
    `${API}/events`,
    guarded(({ request }) => {
      const scope = new URL(request.url).searchParams.get('scope') ?? 'upcoming';
      const now = Date.now();
      const items = mockDb()
        .events.filter((event) => (scope === 'past' ? event.endsAt < now : event.endsAt >= now))
        .sort((a, b) => (scope === 'past' ? b.startsAt - a.startsAt : a.startsAt - b.startsAt))
        .map(serializeEvent);
      return HttpResponse.json({ items });
    }),
  ),
  http.get(
    `${API}/events/:id`,
    guarded(({ params }) => {
      const event = findEvent(params.id);
      return event ? HttpResponse.json(serializeEvent(event)) : apiError('not_found');
    }),
  ),
  http.put(
    `${API}/events/:id/rsvp`,
    guarded(({ params }) => {
      const event = findEvent(params.id);
      if (!event) return apiError('not_found');
      mockDb().rsvps.add(event.id);
      return HttpResponse.json(serializeEvent(event));
    }),
  ),
  http.delete(
    `${API}/events/:id/rsvp`,
    guarded(({ params }) => {
      const event = findEvent(params.id);
      if (!event) return apiError('not_found');
      mockDb().rsvps.delete(event.id);
      return HttpResponse.json(serializeEvent(event));
    }),
  ),

  http.post(
    `${API}/checkins`,
    guarded(async ({ request }) => checkin((await request.json()) as CheckinRequest)),
  ),

  http.get(
    `${API}/org/events`,
    guarded(() => {
      const db = mockDb();
      if (!db.me.isOrganizer) return apiError('forbidden');
      const items = db.events
        .filter((event) => event.organizerId === db.me.id)
        .sort((a, b) => a.startsAt - b.startsAt)
        .map(serializeEvent);
      return HttpResponse.json({ items });
    }),
  ),
  http.post(
    `${API}/org/events`,
    guarded(async ({ request }) => {
      const db = mockDb();
      if (!db.me.isOrganizer) return apiError('forbidden');
      const body = (await request.json()) as CreateEventRequest;
      if (validateEventFields(body) !== null) return apiError('validation_error');
      const kind = db.kinds.find((k) => k.key === body.kind);
      const event: MockEvent = {
        id: db.nextEventId,
        title: body.title,
        description: body.description,
        kind: body.kind,
        location: body.location,
        startsAt: Date.parse(body.starts_at),
        endsAt: Date.parse(body.ends_at),
        points: body.points ?? kind?.defaultPoints ?? 0,
        onboardingStep: body.onboarding_step ?? null,
        organizerId: db.me.id,
        checkinOpen: false,
        others: [],
        otherRsvps: 0,
      };
      db.nextEventId += 1;
      db.events.push(event);
      return HttpResponse.json(serializeEvent(event), { status: 201 });
    }),
  ),
  http.patch(
    `${API}/org/events/:id`,
    guarded(async ({ params, request }) => {
      const event = findOwnEvent(params.id);
      if (typeof event === 'string') return apiError(event);
      const body = (await request.json()) as UpdateEventRequest;
      if (validateEventFields(body, event) !== null) return apiError('validation_error');
      applyEventFields(event, body);
      return HttpResponse.json(serializeEvent(event));
    }),
  ),
  http.get(
    `${API}/org/events/:id/qr`,
    guarded(({ params }) => {
      const event = findOwnEvent(params.id);
      if (typeof event === 'string') return apiError(event);
      if (!event.checkinOpen) return apiError('checkin_closed');
      const window = currentWindow(Date.now());
      const code = mockCode(event.id, window);
      const startedAt = window * MOCK_STEP_SECONDS * 1000;
      const body: EventQr = {
        code,
        deeplink: `https://max.ru/${MOCK_BOT_USERNAME}?startapp=ci_${event.id}_${code}`,
        window_started_at: toIso(startedAt),
        expires_at: toIso(startedAt + MOCK_STEP_SECONDS * 1000),
        step_seconds: MOCK_STEP_SECONDS,
      };
      return HttpResponse.json(body, { headers: { Date: new Date().toUTCString() } });
    }),
  ),
  http.post(
    `${API}/org/events/:id/qr/chat`,
    guarded(({ params }) => {
      const event = findOwnEvent(params.id);
      if (typeof event === 'string') return apiError(event);
      if (!event.checkinOpen) return apiError('checkin_closed');
      return new HttpResponse(null, { status: 202 });
    }),
  ),
  http.get(
    `${API}/org/events/:id/attendance`,
    guarded(({ params }) => {
      const event = findOwnEvent(params.id);
      if (typeof event === 'string') return apiError(event);
      const db = mockDb();
      const mine = db.checkins.get(event.id);
      const items = [
        ...event.others.map((o) => ({
          user_id: o.userId,
          first_name: o.firstName,
          method: o.method,
          checked_in_at: toIso(o.at),
        })),
        ...(mine
          ? [
              {
                user_id: db.me.id,
                first_name: db.me.firstName,
                method: mine.method,
                checked_in_at: toIso(mine.at),
              },
            ]
          : []),
      ];
      const body: Attendance = {
        items,
        rsvp_count: event.otherRsvps + (db.rsvps.has(event.id) ? 1 : 0),
        checkin_count: items.length,
      };
      return HttpResponse.json(body);
    }),
  ),
  http.get(
    `${API}/org/events/:id/attendance.csv`,
    guarded(({ params }) => {
      const event = findOwnEvent(params.id);
      if (typeof event === 'string') return apiError(event);
      const rows = event.others.map(
        (o) => `${o.userId},${o.firstName},${o.method},${toIso(o.at)}`,
      );
      const csv = `﻿user_id,first_name,method,checked_in_at\n${rows.join('\n')}\n`;
      return new HttpResponse(csv, { headers: { 'Content-Type': 'text/csv; charset=utf-8' } });
    }),
  ),

  http.post(
    `${API}/org/invites`,
    guarded(() => {
      const db = mockDb();
      if (!db.me.isAdmin) return apiError('forbidden');
      db.inviteCounter += 1;
      const token = `demo-invite-token-${db.inviteCounter}`;
      const body: Invite = {
        token,
        deeplink: `https://max.ru/${MOCK_BOT_USERNAME}?start=org_${token}`,
        expires_at: toIso(Date.now() + 7 * 24 * 60 * 60 * 1000),
      };
      return HttpResponse.json(body, { status: 201 });
    }),
  ),
];
