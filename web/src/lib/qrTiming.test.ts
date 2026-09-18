import {
  computeQrRefreshDelay,
  estimateClockOffset,
  QR_MIN_DELAY_MS,
  QR_REFRESH_GUARD_MS,
  secondsLeft,
  type QrTimingInput,
} from './qrTiming';

const WINDOW_START = Date.parse('2026-09-17T12:00:00.000Z');
const EXPIRES = WINDOW_START + 10_000;

function input(overrides: Partial<QrTimingInput> = {}): QrTimingInput {
  return {
    windowStartedAt: new Date(WINDOW_START).toISOString(),
    expiresAt: new Date(EXPIRES).toISOString(),
    stepSeconds: 10,
    receivedAt: WINDOW_START + 3_000,
    serverDate: null,
    ...overrides,
  };
}

describe('estimateClockOffset', () => {
  it('trusts the local clock when it falls inside the server window', () => {
    expect(estimateClockOffset(input())).toBe(0);
  });

  it('assumes the window start when the local clock is ahead of the window', () => {
    const receivedAt = EXPIRES + 60_000;
    expect(estimateClockOffset(input({ receivedAt }))).toBe(receivedAt - WINDOW_START);
  });

  it('assumes the window start when the local clock is behind the window', () => {
    const receivedAt = WINDOW_START - 90_000;
    expect(estimateClockOffset(input({ receivedAt }))).toBe(-90_000);
  });

  it('uses the Date header, compensating for its whole-second resolution', () => {
    const serverDate = WINDOW_START + 4_000;
    const receivedAt = serverDate + 500 + 120_000; // client is two minutes ahead
    expect(estimateClockOffset(input({ receivedAt, serverDate }))).toBe(120_000);
  });

  it('clamps a Date header that lies outside the window', () => {
    const receivedAt = WINDOW_START + 2_000;
    expect(estimateClockOffset(input({ receivedAt, serverDate: WINDOW_START - 5_000 }))).toBe(
      2_000,
    );
    expect(estimateClockOffset(input({ receivedAt, serverDate: EXPIRES + 5_000 }))).toBe(
      receivedAt - (EXPIRES - 1),
    );
  });

  it('returns 0 for a malformed window', () => {
    expect(estimateClockOffset(input({ expiresAt: 'nope' }))).toBe(0);
    expect(estimateClockOffset(input({ expiresAt: new Date(WINDOW_START).toISOString() }))).toBe(0);
  });
});

describe('computeQrRefreshDelay', () => {
  it('refreshes right after expires_at when clocks agree', () => {
    const data = input();
    expect(computeQrRefreshDelay(data, data.receivedAt)).toBe(7_000 + QR_REFRESH_GUARD_MS);
  });

  it('shifts the refresh by the clock offset when the client clock is ahead', () => {
    const offset = 45_000;
    const data = input({ receivedAt: WINDOW_START + offset });
    // Estimated server time = window start, so a full step remains.
    expect(computeQrRefreshDelay(data, data.receivedAt)).toBe(10_000 + QR_REFRESH_GUARD_MS);
  });

  it('shifts the refresh by the Date header offset', () => {
    const serverDate = WINDOW_START + 8_000;
    const data = input({ serverDate, receivedAt: serverDate + 500 - 30_000 });
    expect(computeQrRefreshDelay(data, data.receivedAt)).toBe(1_500 + QR_REFRESH_GUARD_MS);
  });

  it('accounts for time that passed after the response arrived', () => {
    const data = input();
    expect(computeQrRefreshDelay(data, data.receivedAt + 5_000)).toBe(2_000 + QR_REFRESH_GUARD_MS);
  });

  it('never schedules faster than the minimum delay', () => {
    const data = input();
    expect(computeQrRefreshDelay(data, EXPIRES + 5_000)).toBe(QR_MIN_DELAY_MS);
  });

  it('never waits longer than one step', () => {
    const data = input({ serverDate: WINDOW_START });
    expect(computeQrRefreshDelay(data, data.receivedAt - 60_000)).toBe(
      10_000 + QR_REFRESH_GUARD_MS,
    );
  });

  it('falls back to a retry delay for an unusable response', () => {
    expect(computeQrRefreshDelay(input({ expiresAt: 'garbage' }), WINDOW_START)).toBe(3_000);
  });
});

describe('secondsLeft', () => {
  it('counts down whole seconds and stops at zero', () => {
    const data = input();
    expect(secondsLeft(data, WINDOW_START + 3_000)).toBe(7);
    expect(secondsLeft(data, WINDOW_START + 3_001)).toBe(7);
    expect(secondsLeft(data, EXPIRES - 1)).toBe(1);
    expect(secondsLeft(data, EXPIRES + 1_000)).toBe(0);
  });
});
