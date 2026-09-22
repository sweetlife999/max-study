import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

import { server } from '../mocks/server';

import { FakeBridge } from '../bridge/fake';
import { currentWindow, MOCK_BOT_USERNAME, mockCode, mockDb } from '../mocks/db';
import { renderApp, type RenderAppOptions } from '../test/render';

/** Event 1 of the mock seed: check-in open and inside its time window. */
const OPEN_EVENT_ID = 1;
/** Event 2: check-in closed. */
const CLOSED_EVENT_ID = 2;
/** Event 4: already checked in by the seeded user. */
const CHECKED_IN_EVENT_ID = 4;

function validCode(eventId: number): string {
  return mockCode(eventId, currentWindow(Date.now()));
}

function consent(): void {
  mockDb().me.consent = true;
}

describe('automatic check-in from a start_param', () => {
  beforeEach(consent);

  it('checks the student in and shows the points earned', async () => {
    const onLocation = vi.fn<NonNullable<RenderAppOptions['onLocation']>>();
    renderApp({
      onLocation,
      bridgeOptions: { startParam: `ci_${OPEN_EVENT_ID}_${validCode(OPEN_EVENT_ID)}` },
    });

    expect(await screen.findByText('Вы отметились')).toBeInTheDocument();
    expect(screen.getByText(/\+10 баллов/)).toBeInTheDocument();
    // Always a way forward.
    expect(screen.getByRole('button', { name: 'На главную' })).toBeInTheDocument();
    expect(mockDb().checkins.has(OPEN_EVENT_ID)).toBe(true);
    expect(onLocation.mock.calls.map(([location]) => location.pathname)).toContain('/checkin/qr');
    expect(JSON.stringify(onLocation.mock.calls)).not.toContain(validCode(OPEN_EVENT_ID));
  });

  it('reports a repeated check-in as success, not as an error', async () => {
    renderApp({
      bridgeOptions: {
        startParam: `ci_${CHECKED_IN_EVENT_ID}_${validCode(CHECKED_IN_EVENT_ID)}`,
      },
    });

    expect(await screen.findByText('Вы уже отмечены')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'На главную' })).toBeInTheDocument();
  });

  it('asks for a fresh code when the code is stale', async () => {
    renderApp({ bridgeOptions: { startParam: `ci_${OPEN_EVENT_ID}_000000` } });

    expect(await screen.findByText('Код устарел')).toBeInTheDocument();
    expect(screen.getByText(/попросите организатора показать новый/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Попробовать ещё раз' })).toBeInTheDocument();
  });

  it('explains that check-in is closed', async () => {
    renderApp({
      bridgeOptions: { startParam: `ci_${CLOSED_EVENT_ID}_${validCode(CLOSED_EVENT_ID)}` },
    });

    expect(await screen.findByText('Отметка закрыта')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'К активности' })).toBeInTheDocument();
  });

  it('reports an unknown event', async () => {
    renderApp({ bridgeOptions: { startParam: 'ci_9999_123456' } });

    expect(await screen.findByText('Активность не найдена')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'На главную' })).toBeInTheDocument();
  });

  it('shows a rate-limit message after too many attempts', async () => {
    const db = mockDb();
    db.attempts = Array.from({ length: 10 }, () => Date.now());

    renderApp({ bridgeOptions: { startParam: `ci_${OPEN_EVENT_ID}_000000` } });

    expect(await screen.findByText('Слишком много попыток')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'На главную' })).toBeInTheDocument();
  });

  it.each([['ci_0_123456'], ['ci_1_12345'], ['ci_abc_123456'], ['ci_']])(
    'explains the malformed payload %s instead of failing silently',
    async (startParam) => {
      renderApp({ bridgeOptions: { startParam } });

      expect(await screen.findByText('Ссылка не распознана')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Ввести код вручную' })).toBeInTheDocument();
    },
  );

  it('opens the main screen for a start_param that is not a check-in', async () => {
    renderApp({ bridgeOptions: { startParam: 'promo_summer2025' } });

    expect(await screen.findByRole('heading', { name: /Привет, Демо/ })).toBeInTheDocument();
  });

  it('shows a network failure and retries only after an explicit request', async () => {
    let calls = 0;
    server.use(
      http.post('*/api/checkins', () => {
        calls += 1;
        return HttpResponse.error();
      }),
    );
    const { user } = renderApp({
      bridgeOptions: { startParam: `ci_${OPEN_EVENT_ID}_${validCode(OPEN_EVENT_ID)}` },
    });

    expect(await screen.findByText(/Нет связи с сервером/)).toBeInTheDocument();
    expect(calls).toBe(1);
    expect(mockDb().checkins.has(OPEN_EVENT_ID)).toBe(false);
    server.resetHandlers();
    await user.click(screen.getByRole('button', { name: 'Повторить' }));
    expect(await screen.findByText('Вы отметились')).toBeInTheDocument();
    expect(mockDb().attempts).toHaveLength(1);
  });

  it('sends exactly one check-in request per payload', async () => {
    const code = validCode(OPEN_EVENT_ID);
    renderApp({ bridgeOptions: { startParam: `ci_${OPEN_EVENT_ID}_${code}` } });

    await screen.findByText('Вы отметились');
    // The rate-limit counter records one attempt per POST /api/checkins.
    expect(mockDb().attempts).toHaveLength(1);
  });
});

it('opens the event card passed by the chat Open app button', async () => {
  consent();
  const onLocation = vi.fn<NonNullable<RenderAppOptions['onLocation']>>();
  renderApp({ bridgeOptions: { startParam: 'ev_1' }, onLocation });

  await waitFor(() =>
    expect(onLocation.mock.calls.map(([location]) => location.pathname)).toContain('/events/1'),
  );
  expect(await screen.findByText('Открытое заседание студсовета')).toBeInTheDocument();
});

describe('manual check-in', () => {
  beforeEach(consent);

  it('accepts six digits for the single open event', async () => {
    const { user } = renderApp({ route: '/checkin' });

    const code = await screen.findByLabelText('Код');
    await user.type(code, validCode(OPEN_EVENT_ID));
    await user.click(screen.getByRole('button', { name: 'Отметиться' }));

    expect(await screen.findByText('Вы отметились')).toBeInTheDocument();
  });

  it('rejects a code that is not six digits before sending anything', async () => {
    const { user } = renderApp({ route: '/checkin' });

    const code = await screen.findByLabelText('Код');
    await user.type(code, '12345');
    await user.click(screen.getByRole('button', { name: 'Отметиться' }));

    expect(await screen.findByText('Код — ровно шесть цифр')).toBeInTheDocument();
    expect(mockDb().attempts).toHaveLength(0);
  });

  it('offers manual entry when the scanner is unavailable', async () => {
    renderApp({ route: '/checkin', bridgeOptions: { canScanQr: false } });

    expect(await screen.findByText(/Сканер недоступен/)).toBeInTheDocument();
    expect(await screen.findByLabelText('Код')).toBeInTheDocument();
  });
});

describe('scanning a QR code', () => {
  beforeEach(consent);

  it('extracts the payload from a MAX deeplink and checks in', async () => {
    const code = validCode(OPEN_EVENT_ID);
    const bridge = new FakeBridge({
      canScanQr: true,
      scan: () =>
        Promise.resolve({
          status: 'scanned',
          text: `https://max.ru/${MOCK_BOT_USERNAME}?startapp=ci_${OPEN_EVENT_ID}_${code}`,
        }),
    });
    const { user } = renderApp({ route: '/checkin', bridge });

    await user.click(await screen.findByRole('button', { name: 'Открыть камеру' }));

    expect(await screen.findByText('Вы отметились')).toBeInTheDocument();
    expect(bridge.scanCalls).toBe(1);
  });

  it('explains a QR code that is not a check-in code', async () => {
    const bridge = new FakeBridge({
      canScanQr: true,
      scan: () => Promise.resolve({ status: 'scanned', text: 'https://example.com/promo' }),
    });
    const { user } = renderApp({ route: '/checkin', bridge });

    await user.click(await screen.findByRole('button', { name: 'Открыть камеру' }));

    expect(await screen.findByText(/Это не QR-код отметки/)).toBeInTheDocument();
  });

  it('falls back to manual entry when the reader is not supported', async () => {
    const bridge = new FakeBridge({
      canScanQr: true,
      scan: () => Promise.resolve({ status: 'unavailable' }),
    });
    const { user } = renderApp({ route: '/checkin', bridge });

    await user.click(await screen.findByRole('button', { name: 'Открыть камеру' }));

    expect(await screen.findByText(/Не удалось открыть сканер/)).toBeInTheDocument();
    expect(screen.getByLabelText('Код')).toBeInTheDocument();
  });

  it('stays put when the user cancels the scan', async () => {
    const bridge = new FakeBridge({
      canScanQr: true,
      scan: () => Promise.resolve({ status: 'cancelled' }),
    });
    const { user } = renderApp({ route: '/checkin', bridge });

    await user.click(await screen.findByRole('button', { name: 'Открыть камеру' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Открыть камеру' })).toBeEnabled();
    });
    expect(screen.queryByText(/Не удалось открыть сканер/)).not.toBeInTheDocument();
    expect(mockDb().attempts).toHaveLength(0);
  });
});
