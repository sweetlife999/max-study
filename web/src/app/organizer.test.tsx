import { screen, waitFor, within } from '@testing-library/react';
import { http } from 'msw';

import { mockDb } from '../mocks/db';
import { apiError } from '../mocks/handlers';
import { server } from '../mocks/server';
import { renderApp } from '../test/render';

const OPEN_EVENT_ID = 1;
const CLOSED_EVENT_ID = 2;

function consent(): void {
  mockDb().me.consent = true;
}

describe('the organizer event list', () => {
  beforeEach(consent);

  it('lists only the events of this organizer', async () => {
    renderApp({ route: '/org' });

    expect(await screen.findByText('Открытое заседание студсовета')).toBeInTheDocument();
    expect(screen.getByText('Встреча с куратором группы')).toBeInTheDocument();
    // Event 3 belongs to another organizer.
    expect(screen.queryByText('Клуб настольных игр')).not.toBeInTheDocument();
  });

  it('is hidden from a student', async () => {
    mockDb().me.isOrganizer = false;
    renderApp({ route: '/org' });

    expect(await screen.findByText('Ничего не нашлось')).toBeInTheDocument();
  });

  it('shows an empty state with a way to create the first event', async () => {
    mockDb().events = [];
    renderApp({ route: '/org' });

    expect(await screen.findByText('Событий пока нет')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Создать событие' })).toBeInTheDocument();
  });
});

describe('creating an event', () => {
  beforeEach(consent);

  it('refuses an end that is not after the start', async () => {
    const { user } = renderApp({ route: '/org/events/new' });

    await user.type(await screen.findByLabelText('Название'), 'Новое событие');
    await user.selectOptions(screen.getByLabelText('Вид активности'), 'council');
    const starts = screen.getByLabelText('Начало');
    const ends = screen.getByLabelText('Окончание');
    await user.clear(starts);
    await user.type(starts, '2026-10-01T10:00');
    await user.clear(ends);
    await user.type(ends, '2026-10-01T09:00');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(await screen.findByText('Окончание должно быть позже начала')).toBeInTheDocument();
    expect(mockDb().events.some((event) => event.title === 'Новое событие')).toBe(false);
  });

  it('requires a title and an activity kind', async () => {
    const { user } = renderApp({ route: '/org/events/new' });

    await user.click(await screen.findByRole('button', { name: 'Сохранить' }));

    expect(await screen.findByText('Введите название')).toBeInTheDocument();
    expect(screen.getByText('Выберите вид активности')).toBeInTheDocument();
  });

  it('creates the event and opens its management screen', async () => {
    const { user } = renderApp({ route: '/org/events/new' });

    await user.type(await screen.findByLabelText('Название'), 'Посвящение в студенты');
    await user.selectOptions(screen.getByLabelText('Вид активности'), 'council');
    await user.type(screen.getByLabelText('Место'), 'Актовый зал');
    const starts = screen.getByLabelText('Начало');
    const ends = screen.getByLabelText('Окончание');
    await user.clear(starts);
    await user.type(starts, '2026-10-01T10:00');
    await user.clear(ends);
    await user.type(ends, '2026-10-01T12:00');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(await screen.findByRole('heading', { name: 'Управление событием' })).toBeInTheDocument();
    const created = mockDb().events.find((event) => event.title === 'Посвящение в студенты');
    expect(created).toBeDefined();
    // 10:00 Moscow time is 07:00 UTC.
    expect(new Date(created?.startsAt ?? 0).toISOString()).toBe('2026-10-01T07:00:00.000Z');
  });

  it('offers the onboarding steps returned by GET /api/onboarding', async () => {
    renderApp({ route: '/org/events/new' });

    const select = await screen.findByLabelText('Шаг онбординга');
    await waitFor(() => {
      expect(within(select).getByRole('option', { name: 'Вступить в чат группы' })).toBeDefined();
    });
  });
});

describe('managing an event', () => {
  beforeEach(consent);

  it('opens and closes check-in with the switch', async () => {
    const { user } = renderApp({ route: `/org/events/${CLOSED_EVENT_ID}` });

    const toggle = await screen.findByLabelText('Отметка открыта');
    expect(toggle).not.toBeChecked();

    await user.click(toggle);

    await waitFor(() => {
      expect(mockDb().events.find((e) => e.id === CLOSED_EVENT_ID)?.checkinOpen).toBe(true);
    });
  });

  it('keeps the QR button disabled while check-in is closed', async () => {
    renderApp({ route: `/org/events/${CLOSED_EVENT_ID}` });

    expect(await screen.findByRole('button', { name: 'Показать QR' })).toBeDisabled();
  });
});

describe('attendance', () => {
  beforeEach(consent);

  it('shows the counters and every attendee', async () => {
    renderApp({ route: `/org/events/${OPEN_EVENT_ID}/attendance` });

    expect(await screen.findByText('Отметились: 4')).toBeInTheDocument();
    expect(screen.getByText('Собирались прийти: 12')).toBeInTheDocument();
    expect(screen.getByText('Анна')).toBeInTheDocument();
    expect(screen.getAllByText('QR').length).toBeGreaterThan(0);
  });

  it('downloads the CSV and can show it as text when saving is unavailable', async () => {
    // jsdom has no object URLs, so the download falls back to the on-screen text.
    const { user } = renderApp({ route: `/org/events/${OPEN_EVENT_ID}/attendance` });

    await user.click(await screen.findByRole('button', { name: 'Скачать CSV' }));

    const csv = await screen.findByLabelText<HTMLTextAreaElement>('Скачать CSV');
    expect(csv.value).toContain('user_id,first_name,method,checked_in_at');
    // `Blob.text()` strips the §7 BOM while decoding; the saved blob itself keeps it.
    expect(csv.value).toContain('Анна');
  });

  it('shows an empty state when nobody checked in', async () => {
    const event = mockDb().events.find((e) => e.id === OPEN_EVENT_ID);
    if (event) event.others = [];
    renderApp({ route: `/org/events/${OPEN_EVENT_ID}/attendance` });

    expect(await screen.findByText('Пока никто не отметился')).toBeInTheDocument();
  });

  it('offers a retry when attendance fails to load', async () => {
    server.use(http.get('*/api/org/events/:id/attendance', () => apiError('forbidden')));
    renderApp({ route: `/org/events/${OPEN_EVENT_ID}/attendance` });

    expect(await screen.findByRole('button', { name: 'Повторить' })).toBeInTheDocument();
  });
});

describe('the admin invite screen', () => {
  beforeEach(consent);

  it('creates an invite and shows its deeplink', async () => {
    const { user } = renderApp({ route: '/admin/invite' });

    await user.click(await screen.findByRole('button', { name: 'Создать приглашение' }));

    expect(await screen.findByText(/start=org_demo-invite-token-1/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Отправить в MAX' })).toBeInTheDocument();
  });

  it('shares the deeplink through the bridge', async () => {
    const { user, bridge } = renderApp({ route: '/admin/invite' });

    await user.click(await screen.findByRole('button', { name: 'Создать приглашение' }));
    await user.click(await screen.findByRole('button', { name: 'Отправить в MAX' }));

    await waitFor(() => {
      expect(bridge.shares).toHaveLength(1);
    });
    expect(bridge.shares[0]?.link).toContain('org_demo-invite-token-1');
  });

  it('tells the admin to copy the link when sharing is unavailable', async () => {
    const { user } = renderApp({
      route: '/admin/invite',
      bridgeOptions: { shareOutcome: 'unavailable' },
    });

    await user.click(await screen.findByRole('button', { name: 'Создать приглашение' }));
    await user.click(await screen.findByRole('button', { name: 'Отправить в MAX' }));

    expect(await screen.findByText(/скопируйте ссылку/i)).toBeInTheDocument();
  });

  it('is hidden from a non-admin', async () => {
    mockDb().me.isAdmin = false;
    renderApp({ route: '/admin/invite' });

    expect(await screen.findByText('Ничего не нашлось')).toBeInTheDocument();
  });
});
