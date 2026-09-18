/**
 * In-memory backend used by MSW in tests and in `npm run dev:mock`. Mirrors the rules of
 * docs/ARCHITECTURE.md §4–§5 closely enough to exercise every UI branch. All data is synthetic.
 */
import type { CheckinMethod, Lang, OnboardingStepType } from '../api/types';

export const MOCK_STEP_SECONDS = 10;
export const MOCK_TOLERANCE_STEPS = 2;
export const MOCK_BOT_USERNAME = 'campus_demo_bot';
export const MOCK_RATE_LIMIT = { attempts: 10, windowMs: 10 * 60 * 1000 };
const CHECKIN_MARGIN_MS = 30 * 60 * 1000;
const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;

type Localized = Record<Lang, string>;

export interface MockKind {
  key: string;
  title: Localized;
  defaultPoints: number;
}

export interface MockStep {
  key: string;
  type: OnboardingStepType;
  eventKind?: string;
  title: Localized;
  description: Localized;
}

export interface MockEvent {
  id: number;
  title: string;
  description: string;
  kind: string;
  location: string;
  startsAt: number;
  endsAt: number;
  points: number;
  onboardingStep: string | null;
  organizerId: number;
  checkinOpen: boolean;
  /** Other (synthetic) attendees, not counting the current user. */
  others: { userId: number; firstName: string; method: CheckinMethod; at: number }[];
  otherRsvps: number;
}

export interface MockDb {
  me: {
    id: number;
    firstName: string;
    lang: Lang;
    consent: boolean;
    isOrganizer: boolean;
    isAdmin: boolean;
  };
  university: { name: Localized; timezone: string };
  kinds: MockKind[];
  steps: MockStep[];
  events: MockEvent[];
  rsvps: Set<number>;
  checkins: Map<number, { method: CheckinMethod; at: number }>;
  manualDone: Set<string>;
  attempts: number[];
  nextEventId: number;
  inviteCounter: number;
}

function seed(now: number): MockDb {
  const meId = 1001;
  const otherOrganizer = 2002;
  const others = (count: number, base: number): MockEvent['others'] =>
    ['Анна', 'Иван', 'Мария', 'Тимур', 'Алия', 'Павел', 'Софья'].slice(0, count).map((name, i) => ({
      userId: 3000 + i,
      firstName: name,
      method: i % 3 === 0 ? 'code' : 'qr',
      at: base + i * 60 * 1000,
    }));

  return {
    me: {
      id: meId,
      firstName: 'Демо',
      lang: 'ru',
      consent: false,
      isOrganizer: true,
      isAdmin: true,
    },
    university: {
      name: { ru: 'Демо-университет', en: 'Demo University' },
      timezone: 'Europe/Moscow',
    },
    kinds: [
      { key: 'council', title: { ru: 'Студсовет', en: 'Student council' }, defaultPoints: 10 },
      {
        key: 'curator_meeting',
        title: { ru: 'Встреча с куратором', en: 'Curator meeting' },
        defaultPoints: 5,
      },
      { key: 'club', title: { ru: 'Клуб', en: 'Club' }, defaultPoints: 5 },
    ],
    steps: [
      {
        key: 'meet_curator',
        type: 'event_kind',
        eventKind: 'curator_meeting',
        title: { ru: 'Познакомиться с куратором', en: 'Meet your curator' },
        description: {
          ru: 'Приходите на встречу с куратором и отметьтесь по QR.',
          en: 'Attend a curator meeting and check in with the QR code.',
        },
      },
      {
        key: 'visit_council',
        type: 'event_kind',
        eventKind: 'council',
        title: { ru: 'Сходить на студсовет', en: 'Visit the student council' },
        description: {
          ru: 'Узнайте, как устроено студенческое самоуправление.',
          en: 'Learn how student government works.',
        },
      },
      {
        key: 'join_group_chat',
        type: 'manual',
        title: { ru: 'Вступить в чат группы', en: 'Join your group chat' },
        description: {
          ru: 'Ссылку на чат даёт куратор или староста.',
          en: 'Your curator or group leader shares the link.',
        },
      },
    ],
    events: [
      {
        id: 1,
        title: 'Открытое заседание студсовета',
        description: 'Знакомство с активом, планы на семестр.',
        kind: 'council',
        location: 'Аудитория 101',
        startsAt: now - 20 * 60 * 1000,
        endsAt: now + HOUR,
        points: 10,
        onboardingStep: null,
        organizerId: meId,
        checkinOpen: true,
        others: others(4, now - 15 * 60 * 1000),
        otherRsvps: 12,
      },
      {
        id: 2,
        title: 'Встреча с куратором группы',
        description: 'Расписание, чаты, ответы на вопросы.',
        kind: 'curator_meeting',
        location: 'Корпус Б, 2 этаж',
        startsAt: now + DAY,
        endsAt: now + DAY + HOUR,
        points: 5,
        onboardingStep: 'meet_curator',
        organizerId: meId,
        checkinOpen: false,
        others: [],
        otherRsvps: 18,
      },
      {
        id: 3,
        title: 'Клуб настольных игр',
        description: '',
        kind: 'club',
        location: 'Коворкинг',
        startsAt: now + 3 * DAY,
        endsAt: now + 3 * DAY + 2 * HOUR,
        points: 5,
        onboardingStep: null,
        organizerId: otherOrganizer,
        checkinOpen: false,
        others: [],
        otherRsvps: 7,
      },
      {
        id: 4,
        title: 'Экскурсия по кампусу',
        description: 'Библиотека, столовая, спортзал.',
        kind: 'club',
        location: 'Главный вход',
        startsAt: now - 3 * DAY,
        endsAt: now - 3 * DAY + HOUR,
        points: 5,
        onboardingStep: null,
        organizerId: otherOrganizer,
        checkinOpen: false,
        others: others(7, now - 3 * DAY),
        otherRsvps: 25,
      },
    ],
    rsvps: new Set([3]),
    checkins: new Map([[4, { method: 'qr', at: now - 3 * DAY + 10 * 60 * 1000 }]]),
    manualDone: new Set(),
    attempts: [],
    nextEventId: 5,
    inviteCounter: 0,
  };
}

let db: MockDb = seed(Date.now());

export function mockDb(): MockDb {
  return db;
}

export function resetMockDb(now: number = Date.now()): void {
  db = seed(now);
}

export function localize(value: Localized, lang: Lang): string {
  return value[lang];
}

/** Deterministic stand-in for the HMAC code of §5 (the mock has no secrets to protect). */
export function mockCode(eventId: number, window: number): string {
  const value = (eventId * 7919 + window * 104729 + 12345) % 1_000_000;
  return String(value).padStart(6, '0');
}

export function currentWindow(now: number): number {
  return Math.floor(now / 1000 / MOCK_STEP_SECONDS);
}

export function isCodeValid(eventId: number, code: string, now: number): boolean {
  const window = currentWindow(now);
  for (let w = window - MOCK_TOLERANCE_STEPS; w <= window; w += 1) {
    if (mockCode(eventId, w) === code) return true;
  }
  return false;
}

export function isWithinCheckinWindow(event: MockEvent, now: number): boolean {
  return now >= event.startsAt - CHECKIN_MARGIN_MS && now <= event.endsAt + CHECKIN_MARGIN_MS;
}

export function isStepDone(step: MockStep, state: MockDb): boolean {
  if (step.type === 'manual') return state.manualDone.has(step.key);
  return state.events.some(
    (event) =>
      state.checkins.has(event.id) &&
      (event.kind === step.eventKind || event.onboardingStep === step.key),
  );
}
