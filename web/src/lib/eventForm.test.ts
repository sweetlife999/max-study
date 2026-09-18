import type { Event } from '../api/types';
import { emptyEventForm, eventToForm, validateEventForm, type EventForm } from './eventForm';

const TZ = 'Europe/Moscow';

const valid: EventForm = {
  title: 'Студсовет',
  description: ' Планы на семестр ',
  kind: 'council',
  location: ' Аудитория 101 ',
  startsAt: '2026-09-18T14:00',
  endsAt: '2026-09-18T15:30',
  points: '10',
  onboardingStep: '',
};

describe('validateEventForm', () => {
  it('builds a request with UTC times converted from the university time zone', () => {
    const { errors, request } = validateEventForm(valid, TZ);
    expect(errors).toEqual({});
    expect(request).toEqual({
      title: 'Студсовет',
      description: 'Планы на семестр',
      kind: 'council',
      location: 'Аудитория 101',
      // Moscow is UTC+3 all year.
      starts_at: '2026-09-18T11:00:00.000Z',
      ends_at: '2026-09-18T12:30:00.000Z',
      points: 10,
      onboarding_step: null,
    });
  });

  it('omits points when the field is left empty', () => {
    const { request } = validateEventForm({ ...valid, points: '  ' }, TZ);
    expect(request).not.toHaveProperty('points');
  });

  it('passes the selected onboarding step through', () => {
    const { request } = validateEventForm({ ...valid, onboardingStep: 'meet_curator' }, TZ);
    expect(request?.onboarding_step).toBe('meet_curator');
  });

  it('requires a title', () => {
    const { errors, request } = validateEventForm({ ...valid, title: '   ' }, TZ);
    expect(errors.title).toBe('required');
    expect(request).toBeNull();
  });

  it('requires a kind', () => {
    const { errors, request } = validateEventForm({ ...valid, kind: '' }, TZ);
    expect(errors.kind).toBe('required');
    expect(request).toBeNull();
  });

  it.each([['2026-13-01T10:00'], ['2026-02-30T10:00'], ['not-a-date'], ['']])(
    'rejects the start %s',
    (startsAt) => {
      const { errors, request } = validateEventForm({ ...valid, startsAt }, TZ);
      expect(errors.startsAt).toBe('invalid');
      expect(request).toBeNull();
    },
  );

  it.each([
    ['2026-09-18T14:00', 'before_start'],
    ['2026-09-18T13:59', 'before_start'],
    ['2026-09-17T23:00', 'before_start'],
  ])('rejects the end %s as %s', (endsAt, code) => {
    const { errors, request } = validateEventForm({ ...valid, endsAt }, TZ);
    expect(errors.endsAt).toBe(code);
    expect(request).toBeNull();
  });

  it.each([['-1'], ['1.5'], ['abc'], ['1e3'], ['١٠']])('rejects the points value %s', (points) => {
    const { errors, request } = validateEventForm({ ...valid, points }, TZ);
    expect(errors.points).toBe('invalid');
    expect(request).toBeNull();
  });

  it('accepts zero points', () => {
    const { errors, request } = validateEventForm({ ...valid, points: '0' }, TZ);
    expect(errors).toEqual({});
    expect(request?.points).toBe(0);
  });

  it('respects a non-Moscow time zone', () => {
    const { request } = validateEventForm(valid, 'Asia/Yekaterinburg'); // UTC+5
    expect(request?.starts_at).toBe('2026-09-18T09:00:00.000Z');
  });
});

describe('emptyEventForm', () => {
  it('starts at the next full hour in the university time zone', () => {
    const now = Date.parse('2026-09-18T11:12:00.000Z');
    const form = emptyEventForm(TZ, now);
    expect(form.startsAt).toBe('2026-09-18T15:00');
    expect(form.endsAt).toBe('2026-09-18T16:00');
    expect(validateEventForm(form, TZ).errors.startsAt).toBeUndefined();
  });
});

describe('eventToForm', () => {
  it('round-trips an event through the form', () => {
    const event: Event = {
      id: 7,
      title: 'Клуб',
      description: 'Игры',
      kind: 'club',
      kind_title: 'Клуб',
      location: 'Коворкинг',
      starts_at: '2026-09-18T11:00:00.000Z',
      ends_at: '2026-09-18T12:30:00.000Z',
      points: 5,
      onboarding_step: 'meet_curator',
      checkin_open: false,
      rsvp: false,
      checked_in: false,
      attendees_count: 0,
    };
    const form = eventToForm(event, TZ);
    expect(form.startsAt).toBe('2026-09-18T14:00');
    expect(form.endsAt).toBe('2026-09-18T15:30');
    expect(form.points).toBe('5');
    expect(form.onboardingStep).toBe('meet_curator');

    const { request } = validateEventForm(form, TZ);
    expect(request?.starts_at).toBe(event.starts_at);
    expect(request?.ends_at).toBe(event.ends_at);
  });
});
