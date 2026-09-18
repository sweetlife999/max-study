import {
  formatDateTime,
  formatRange,
  isValidTimeZone,
  wallTimeToUtcIso,
  zonedWallTime,
} from './datetime';

describe('zonedWallTime', () => {
  it('renders the wall time in the university zone, not the device zone', () => {
    expect(zonedWallTime('2026-09-17T09:30:00Z', 'Europe/Moscow')).toBe('2026-09-17T12:30');
    expect(zonedWallTime('2026-09-17T09:30:00Z', 'Asia/Vladivostok')).toBe('2026-09-17T19:30');
    expect(zonedWallTime('2026-09-17T23:30:00Z', 'Asia/Yekaterinburg')).toBe('2026-09-18T04:30');
  });

  it('returns an empty string for an invalid date', () => {
    expect(zonedWallTime('not a date', 'Europe/Moscow')).toBe('');
  });
});

describe('wallTimeToUtcIso', () => {
  it('converts a wall time in the university zone to UTC', () => {
    expect(wallTimeToUtcIso('2026-09-17T12:30', 'Europe/Moscow')).toBe('2026-09-17T09:30:00.000Z');
    expect(wallTimeToUtcIso('2026-01-01T00:00', 'Asia/Kamchatka')).toBe('2025-12-31T12:00:00.000Z');
  });

  it('round-trips with zonedWallTime', () => {
    const iso = '2026-10-05T15:45:00.000Z';
    for (const zone of ['Europe/Moscow', 'Europe/Kaliningrad', 'Asia/Novosibirsk', 'UTC']) {
      expect(wallTimeToUtcIso(zonedWallTime(iso, zone), zone)).toBe(iso);
    }
  });

  it('handles zones with daylight saving time on both sides of the switch', () => {
    expect(wallTimeToUtcIso('2026-07-01T10:00', 'Europe/Berlin')).toBe('2026-07-01T08:00:00.000Z');
    expect(wallTimeToUtcIso('2026-12-01T10:00', 'Europe/Berlin')).toBe('2026-12-01T09:00:00.000Z');
    // 02:30 does not exist on 2026-03-29 in Berlin; it maps to a real instant after the jump.
    expect(wallTimeToUtcIso('2026-03-29T02:30', 'Europe/Berlin')).not.toBeNull();
  });

  it.each([
    [''],
    ['2026-09-17'],
    ['2026-09-17 12:30'],
    ['2026-02-30T10:00'],
    ['2026-13-01T10:00'],
    ['2026-09-17T24:00'],
    ['2026-09-17T12:60'],
  ])('rejects %j', (value) => {
    expect(wallTimeToUtcIso(value, 'Europe/Moscow')).toBeNull();
  });
});

describe('formatting', () => {
  it('formats in the given zone and language', () => {
    expect(formatDateTime('2026-09-17T09:30:00Z', 'Europe/Moscow', 'ru')).toContain('12:30');
    expect(formatDateTime('2026-09-17T09:30:00Z', 'Asia/Vladivostok', 'en')).toContain('19:30');
  });

  it('shortens a same-day range and keeps both dates otherwise', () => {
    const sameDay = formatRange(
      '2026-09-17T09:00:00Z',
      '2026-09-17T10:30:00Z',
      'Europe/Moscow',
      'ru',
    );
    expect(sameDay).toMatch(/12:00 – 13:30$/);
    const multiDay = formatRange(
      '2026-09-17T09:00:00Z',
      '2026-09-18T10:30:00Z',
      'Europe/Moscow',
      'en',
    );
    expect(multiDay).toContain('17');
    expect(multiDay).toContain('18');
  });

  it('falls back to UTC for an unknown zone instead of throwing', () => {
    expect(isValidTimeZone('Mars/Olympus')).toBe(false);
    expect(formatDateTime('2026-09-17T09:30:00Z', 'Mars/Olympus', 'en')).toContain('09:30');
  });
});
