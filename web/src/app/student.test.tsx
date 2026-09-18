import { screen, waitFor, within } from '@testing-library/react';
import { http } from 'msw';

import { apiError } from '../mocks/handlers';
import { mockDb } from '../mocks/db';
import { server } from '../mocks/server';
import { renderApp } from '../test/render';

const RSVP_EVENT_ID = 2;

function consent(): void {
  mockDb().me.consent = true;
}

describe('consent gate', () => {
  it('blocks every screen until the student agrees', async () => {
    renderApp({ route: '/events' });

    expect(await screen.findByText('Согласие на обработку данных')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Активности' })).not.toBeInTheDocument();
  });

  it('names the data, the purpose and the operator', async () => {
    renderApp();

    expect(await screen.findByText('Какие данные')).toBeInTheDocument();
    expect(screen.getByText('Зачем')).toBeInTheDocument();
    expect(screen.getByText('Оператор')).toBeInTheDocument();
    expect(screen.getAllByText('Демо-университет').length).toBeGreaterThan(0);
  });

  it('lets the language be switched before agreeing', async () => {
    const { user } = renderApp();

    await user.click(await screen.findByRole('button', { name: 'English' }));

    expect(await screen.findByText('Consent to data processing')).toBeInTheDocument();
    expect(mockDb().me.lang).toBe('en');
    expect(document.documentElement).toHaveAttribute('lang', 'en');
  });

  it('opens the app once consent is given', async () => {
    const { user } = renderApp();

    await user.click(await screen.findByRole('button', { name: 'Согласен' }));

    expect(await screen.findByRole('heading', { name: /Привет, Демо/ })).toBeInTheDocument();
    expect(mockDb().me.consent).toBe(true);
  });

  it('returns to the consent screen when the API answers 403 consent_required', async () => {
    consent();
    server.use(http.get('*/api/onboarding', () => apiError('consent_required')));

    renderApp();

    expect(await screen.findByText('Согласие на обработку данных')).toBeInTheDocument();
  });
});

describe('the main screen', () => {
  beforeEach(consent);

  it('shows onboarding progress and the upcoming activities', async () => {
    renderApp();

    expect(await screen.findByText('Онбординг')).toBeInTheDocument();
    // The only seeded check-in is of kind `club`, which no onboarding step asks for.
    expect(screen.getByText('0 из 3')).toBeInTheDocument();
    expect(screen.getByText('Открытое заседание студсовета')).toBeInTheDocument();
  });

  it('closes a manual step with its button', async () => {
    const { user } = renderApp();

    const step = await screen.findByLabelText('Отметить шаг «Вступить в чат группы» выполненным');
    await user.click(step);

    await waitFor(() => {
      expect(mockDb().manualDone.has('join_group_chat')).toBe(true);
    });
    expect(await screen.findByText('1 из 3')).toBeInTheDocument();
  });

  it('offers a retry when onboarding fails to load', async () => {
    let calls = 0;
    server.use(
      http.get('*/api/onboarding', () => {
        calls += 1;
        return apiError('not_found');
      }),
    );
    const { user } = renderApp();

    await user.click(await screen.findByRole('button', { name: 'Повторить' }));

    await waitFor(() => {
      expect(calls).toBeGreaterThan(1);
    });
  });
});

describe('RSVP', () => {
  beforeEach(consent);

  it('records "going" and then "not going"', async () => {
    const { user } = renderApp({ route: `/events/${RSVP_EVENT_ID}` });

    const going = await screen.findByRole('button', { name: 'Иду' });
    expect(going).toHaveAttribute('aria-pressed', 'false');

    await user.click(going);

    await waitFor(() => {
      expect(mockDb().rsvps.has(RSVP_EVENT_ID)).toBe(true);
    });
    expect(await screen.findByText(/Пришлём напоминание/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Не иду' }));

    await waitFor(() => {
      expect(mockDb().rsvps.has(RSVP_EVENT_ID)).toBe(false);
    });
  });

  it('shows an inline error and keeps the screen usable when RSVP fails', async () => {
    server.use(http.put('*/api/events/:id/rsvp', () => apiError('not_found')));
    const { user } = renderApp({ route: `/events/${RSVP_EVENT_ID}` });

    await user.click(await screen.findByRole('button', { name: 'Иду' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Не найдено.');
    expect(screen.getByRole('button', { name: 'Иду' })).toBeEnabled();
  });

  it('hides RSVP for an activity that is over', async () => {
    renderApp({ route: '/events/4' });

    expect(await screen.findByText('Экскурсия по кампусу')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Иду' })).not.toBeInTheDocument();
  });
});

describe('the activity list', () => {
  beforeEach(consent);

  it('switches between upcoming and past activities', async () => {
    const { user } = renderApp({ route: '/events' });

    expect(await screen.findByText('Встреча с куратором группы')).toBeInTheDocument();
    expect(screen.queryByText('Экскурсия по кампусу')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Прошедшие' }));

    expect(await screen.findByText('Экскурсия по кампусу')).toBeInTheDocument();
  });

  it('shows an empty state when there is nothing to list', async () => {
    mockDb().events = [];
    renderApp({ route: '/events' });

    expect(await screen.findByText('Ближайших активностей пока нет')).toBeInTheDocument();
  });
});

describe('the profile', () => {
  beforeEach(consent);

  it('shows points, the university time zone and the language switch', async () => {
    renderApp({ route: '/profile' });

    const list = await screen.findByText('Баллы');
    expect(list).toBeInTheDocument();
    expect(screen.getByText('Europe/Moscow')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'English' })).toBeInTheDocument();
  });

  it('switches the interface language through PATCH /api/me', async () => {
    const { user } = renderApp({ route: '/profile' });

    await user.click(await screen.findByRole('button', { name: 'English' }));

    expect(await screen.findByRole('heading', { name: 'Profile' })).toBeInTheDocument();
    expect(mockDb().me.lang).toBe('en');
    expect(document.documentElement).toHaveAttribute('lang', 'en');
  });
});

describe('unknown routes', () => {
  beforeEach(consent);

  it('offer a way back home', async () => {
    renderApp({ route: '/nowhere' });

    const heading = await screen.findByRole('heading', { name: 'Ничего не нашлось' });
    expect(heading).toBeInTheDocument();
    expect(
      within(document.body).getAllByRole('button', { name: 'На главную' }).length,
    ).toBeGreaterThan(0);
  });
});
