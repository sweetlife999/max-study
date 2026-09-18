import { ApiError, CLIENT_ERROR_CODES, errorFromResponse } from './errors';
import type {
  Attendance,
  CheckinRequest,
  CheckinResult,
  CreateEventRequest,
  Event,
  EventList,
  EventQr,
  EventScope,
  Invite,
  Lang,
  Me,
  Onboarding,
  Step,
  UpdateEventRequest,
} from './types';

export const INIT_DATA_HEADER = 'X-Max-Init-Data';
export const DEFAULT_API_BASE_URL = '/api';

type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface ApiClientOptions {
  /** Base URL, e.g. `/api` or `https://example.org/api`. */
  baseUrl: string;
  /** Raw `WebApp.initData`; an empty string means "not launched inside MAX". */
  getInitData: () => string;
}

/** A QR response together with the local clock reading taken right when it arrived. */
export interface TimedQr {
  qr: EventQr;
  /** `Date.now()` at the moment the response headers were received. */
  receivedAt: number;
  /** Server clock from the HTTP `Date` header (1 s resolution), if readable. */
  serverDate: number | null;
}

export type ApiClient = ReturnType<typeof createApiClient>;

/** Joins the base URL and an endpoint path and resolves it against the page origin. */
export function buildUrl(baseUrl: string, path: string, origin: string): string {
  const base = baseUrl.replace(/\/+$/, '');
  return new URL(`${base}${path}`, origin).toString();
}

export function createApiClient(options: ApiClientOptions) {
  async function send(method: HttpMethod, path: string, body?: unknown): Promise<Response> {
    const headers = new Headers({ Accept: 'application/json' });
    const initData = options.getInitData();
    if (initData) headers.set(INIT_DATA_HEADER, initData);
    if (body !== undefined) headers.set('Content-Type', 'application/json');

    let response: Response;
    try {
      response = await fetch(buildUrl(options.baseUrl, path, window.location.origin), {
        method,
        headers,
        body: body === undefined ? null : JSON.stringify(body),
        credentials: 'same-origin',
      });
    } catch {
      throw new ApiError({ status: 0, code: CLIENT_ERROR_CODES.network });
    }
    if (!response.ok) throw await errorFromResponse(response);
    return response;
  }

  async function json<T>(method: HttpMethod, path: string, body?: unknown): Promise<T> {
    const response = await send(method, path, body);
    try {
      return (await response.json()) as T;
    } catch {
      throw new ApiError({ status: response.status, code: CLIENT_ERROR_CODES.invalidResponse });
    }
  }

  const eventPath = (id: number) => `/events/${encodeURIComponent(String(id))}`;
  const orgEventPath = (id: number) => `/org/events/${encodeURIComponent(String(id))}`;

  return {
    getMe: () => json<Me>('GET', '/me'),
    giveConsent: () => json<Me>('POST', '/me/consent'),
    updateLang: (lang: Lang) => json<Me>('PATCH', '/me', { lang }),

    getOnboarding: () => json<Onboarding>('GET', '/onboarding'),
    completeStep: (key: string) =>
      json<Step>('POST', `/onboarding/${encodeURIComponent(key)}/complete`),

    listEvents: (scope: EventScope) =>
      json<EventList>('GET', `/events?scope=${encodeURIComponent(scope)}`),
    getEvent: (id: number) => json<Event>('GET', eventPath(id)),
    setRsvp: (id: number, going: boolean) =>
      json<Event>(going ? 'PUT' : 'DELETE', `${eventPath(id)}/rsvp`),

    checkin: (request: CheckinRequest) => json<CheckinResult>('POST', '/checkins', request),

    listOrgEvents: () => json<EventList>('GET', '/org/events'),
    createOrgEvent: (request: CreateEventRequest) => json<Event>('POST', '/org/events', request),
    updateOrgEvent: (id: number, request: UpdateEventRequest) =>
      json<Event>('PATCH', orgEventPath(id), request),

    getEventQr: async (id: number): Promise<TimedQr> => {
      const response = await send('GET', `${orgEventPath(id)}/qr`);
      const receivedAt = Date.now();
      const dateHeader = response.headers.get('Date');
      const parsedDate = dateHeader === null ? Number.NaN : Date.parse(dateHeader);
      try {
        const qr = (await response.json()) as EventQr;
        return { qr, receivedAt, serverDate: Number.isFinite(parsedDate) ? parsedDate : null };
      } catch {
        throw new ApiError({ status: response.status, code: CLIENT_ERROR_CODES.invalidResponse });
      }
    },
    sendQrToChat: async (id: number): Promise<void> => {
      await send('POST', `${orgEventPath(id)}/qr/chat`);
    },
    getAttendance: (id: number) => json<Attendance>('GET', `${orgEventPath(id)}/attendance`),
    getAttendanceCsv: async (id: number): Promise<Blob> => {
      const response = await send('GET', `${orgEventPath(id)}/attendance.csv`);
      return response.blob();
    },

    createInvite: () => json<Invite>('POST', '/org/invites'),
  };
}
