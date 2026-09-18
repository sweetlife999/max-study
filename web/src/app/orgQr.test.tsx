import { act, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

import type { EventQr } from '../api/types';
import { MOCK_STEP_SECONDS, mockDb } from '../mocks/db';
import { server } from '../mocks/server';
import { renderApp } from '../test/render';

const OPEN_EVENT_ID = 1;
const CLOSED_EVENT_ID = 2;
const STEP_MS = MOCK_STEP_SECONDS * 1000;

/**
 * A QR endpoint whose windows are aligned to the *server* clock, which we place `skewMs` behind
 * the client. Every response is recorded so the test can check when the refetch happened.
 */
function qrEndpoint(options: { skewMs?: number; failAfter?: number } = {}) {
  const skewMs = options.skewMs ?? 0;
  const responses: EventQr[] = [];
  let calls = 0;

  server.use(
    http.get(`*/api/org/events/${OPEN_EVENT_ID}/qr`, () => {
      calls += 1;
      if (options.failAfter !== undefined && calls > options.failAfter) {
        return new HttpResponse(null, { status: 503 });
      }
      const serverNow = Date.now() - skewMs;
      const windowStart = Math.floor(serverNow / STEP_MS) * STEP_MS;
      const code = String((windowStart / STEP_MS) % 1_000_000).padStart(6, '0');
      const body: EventQr = {
        code,
        deeplink: `https://max.ru/campus_demo_bot?startapp=ci_${OPEN_EVENT_ID}_${code}`,
        window_started_at: new Date(windowStart).toISOString(),
        expires_at: new Date(windowStart + STEP_MS).toISOString(),
        step_seconds: MOCK_STEP_SECONDS,
      };
      responses.push(body);
      return HttpResponse.json(body, { headers: { Date: new Date(serverNow).toUTCString() } });
    }),
  );

  return {
    responses,
    get calls() {
      return calls;
    },
  };
}

/** Lets pending promises settle while fake timers are installed. */
async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe('the full-screen QR', () => {
  beforeEach(() => {
    mockDb().me.consent = true;
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // Start at a window boundary so the first response always has a full step ahead of it.
    vi.setSystemTime(Math.ceil(Date.now() / STEP_MS) * STEP_MS);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows the code, the countdown and the attendance counter', async () => {
    const endpoint = qrEndpoint();
    renderApp({ route: `/org/events/${OPEN_EVENT_ID}/qr`, fakeTimers: true });

    await waitFor(() => {
      expect(endpoint.responses).toHaveLength(1);
    });
    const first = endpoint.responses[0];
    expect(await screen.findByLabelText('Код')).toHaveTextContent(first?.code ?? '');
    expect(screen.getByRole('img', { name: 'QR для отметки' })).toBeInTheDocument();
    expect(await screen.findByText(/Обновится через/)).toBeInTheDocument();
    expect(await screen.findByText('Отметились: 4')).toBeInTheDocument();
  });

  it('re-requests the code exactly at the end of the server window', async () => {
    const endpoint = qrEndpoint();
    renderApp({ route: `/org/events/${OPEN_EVENT_ID}/qr`, fakeTimers: true });

    await waitFor(() => {
      expect(endpoint.responses).toHaveLength(1);
    });
    const expiresAt = Date.parse(endpoint.responses[0]?.expires_at ?? '');

    // A second before the window ends nothing has been re-requested yet.
    await advance(Math.max(expiresAt - Date.now() - 1000, 0));
    expect(endpoint.calls).toBe(1);

    await advance(1500);
    await waitFor(() => {
      expect(endpoint.calls).toBeGreaterThanOrEqual(2);
    });
    const latest = endpoint.responses[endpoint.responses.length - 1];
    expect(latest?.window_started_at).not.toBe(endpoint.responses[0]?.window_started_at);
    expect(await screen.findByLabelText('Код')).toHaveTextContent(latest?.code ?? '');
  });

  it('follows the server clock when the device clock runs ahead', async () => {
    // Device clock is 4 s ahead of the server: a naive timer would refresh 4 s too early.
    const skewMs = 4000;
    const endpoint = qrEndpoint({ skewMs });
    renderApp({ route: `/org/events/${OPEN_EVENT_ID}/qr`, fakeTimers: true });

    await waitFor(() => {
      expect(endpoint.responses).toHaveLength(1);
    });
    const serverExpires = Date.parse(endpoint.responses[0]?.expires_at ?? '');
    const localExpires = serverExpires + skewMs;

    // At the server timestamp (still 4 s of real window left) nothing is refetched.
    await advance(Math.max(serverExpires - Date.now() + 500, 0));
    expect(endpoint.calls).toBe(1);

    await advance(localExpires - Date.now() + 400);
    await waitFor(() => {
      expect(endpoint.calls).toBe(2);
    });
  });

  it('keeps the code on screen and warns when the refresh fails', async () => {
    const endpoint = qrEndpoint({ failAfter: 1 });
    renderApp({ route: `/org/events/${OPEN_EVENT_ID}/qr`, fakeTimers: true });

    await waitFor(() => {
      expect(endpoint.responses).toHaveLength(1);
    });
    const code = endpoint.responses[0]?.code ?? '';

    await advance(STEP_MS + 1000);
    await flush();

    expect(await screen.findByText(/Код не удалось обновить/)).toBeInTheDocument();
    expect(screen.getByLabelText('Код')).toHaveTextContent(code);
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
  });

  it('stops re-requesting the code once the server forbids it and explains why', async () => {
    // §7: `GET /api/org/events/{id}/qr` answers 403 as soon as `checkin_open` is false. The old
    // loop retried that deterministic answer every 3 s forever and left a stale code on screen.
    let calls = 0;
    server.use(
      http.get(`*/api/org/events/${OPEN_EVENT_ID}/qr`, () => {
        calls += 1;
        const event = mockDb().events.find((candidate) => candidate.id === OPEN_EVENT_ID);
        if (event) event.checkinOpen = false;
        return HttpResponse.json(
          { error: { code: 'checkin_closed', message: 'Отметка закрыта.' } },
          { status: 403 },
        );
      }),
    );

    renderApp({ route: `/org/events/${OPEN_EVENT_ID}/qr`, fakeTimers: true });

    expect(await screen.findByText('Отметка закрыта')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Открыть отметку' })).toBeInTheDocument();

    await advance(30_000);
    await flush();
    expect(calls).toBe(1);
  });

  it('stops re-requesting a code the server keeps refusing and offers an explicit retry', async () => {
    // A deterministic 4xx (deleted event, expired initData, lost ownership) answers the same way
    // forever: the old loop re-requested it every 3 s for as long as the screen stayed open.
    let calls = 0;
    server.use(
      http.get(`*/api/org/events/${OPEN_EVENT_ID}/qr`, () => {
        calls += 1;
        return HttpResponse.json(
          { error: { code: 'not_found', message: 'Не найдено.' } },
          { status: 404 },
        );
      }),
    );

    renderApp({ route: `/org/events/${OPEN_EVENT_ID}/qr`, fakeTimers: true });

    expect(await screen.findByRole('button', { name: 'Повторить' })).toBeInTheDocument();
    expect(calls).toBe(1);

    await advance(30_000);
    await flush();
    expect(calls).toBe(1);
  });

  it('refuses to show a QR while check-in is closed and offers a way back', async () => {
    renderApp({ route: `/org/events/${CLOSED_EVENT_ID}/qr`, fakeTimers: true });

    expect(await screen.findByText('Отметка закрыта')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Открыть отметку' })).toBeInTheDocument();
  });

  it('sends the QR to the bot chat on request', async () => {
    qrEndpoint();
    let chatCalls = 0;
    server.use(
      http.post(`*/api/org/events/${OPEN_EVENT_ID}/qr/chat`, () => {
        chatCalls += 1;
        return new HttpResponse(null, { status: 202 });
      }),
    );
    const { user } = renderApp({ route: `/org/events/${OPEN_EVENT_ID}/qr`, fakeTimers: true });

    await user.click(await screen.findByRole('button', { name: 'Показать QR в чате' }));

    await waitFor(() => {
      expect(chatCalls).toBe(1);
    });
    expect(await screen.findByText('QR отправлен в чат с ботом')).toBeInTheDocument();
  });

  it('asks the client for maximum screen brightness while the QR is visible', async () => {
    qrEndpoint();
    const { bridge, unmount } = renderApp({
      route: `/org/events/${OPEN_EVENT_ID}/qr`,
      fakeTimers: true,
    });

    await screen.findByLabelText('Код');
    expect(bridge.brightnessRequested).toBe(true);

    unmount();
    expect(bridge.brightnessRequested).toBe(false);
  });
});
