import type { ApiErrorBody } from './types';

/** Error codes produced by the client itself (never sent by the backend). */
export const CLIENT_ERROR_CODES = {
  network: 'network_error',
  invalidResponse: 'invalid_response',
} as const;

/** Error codes the contract names explicitly (§7). */
export const CONSENT_REQUIRED = 'consent_required';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  /** Server-provided message, already in the user's language (§7). Empty for client errors. */
  readonly serverMessage: string;
  /** Seconds from the `Retry-After` header of a 429 response, if present. */
  readonly retryAfterSeconds: number | null;

  constructor(params: {
    status: number;
    code: string;
    serverMessage?: string;
    retryAfterSeconds?: number | null;
  }) {
    super(`API error ${params.status}: ${params.code}`);
    this.name = 'ApiError';
    this.status = params.status;
    this.code = params.code;
    this.serverMessage = params.serverMessage ?? '';
    this.retryAfterSeconds = params.retryAfterSeconds ?? null;
  }

  get isConsentRequired(): boolean {
    return this.status === 403 && this.code === CONSENT_REQUIRED;
  }

  get isRateLimited(): boolean {
    return this.status === 429;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isNetwork(): boolean {
    return this.code === CLIENT_ERROR_CODES.network;
  }

  /** 4xx errors are deterministic: retrying the same request will not help. */
  get isClientError(): boolean {
    return this.status >= 400 && this.status < 500;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== 'object' || value === null || !('error' in value)) return false;
  const error = value.error;
  return (
    typeof error === 'object' &&
    error !== null &&
    'code' in error &&
    typeof error.code === 'string' &&
    'message' in error &&
    typeof error.message === 'string'
  );
}

/** Parses `Retry-After` given in seconds (the HTTP-date form is ignored). */
export function parseRetryAfter(header: string | null): number | null {
  if (header === null || !/^\s*\d+\s*$/.test(header)) return null;
  return Number.parseInt(header, 10);
}

/** Builds an ApiError from a non-2xx response, tolerating bodies that do not follow §7. */
export async function errorFromResponse(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // Not JSON (HTML error page, empty body): `body` stays null and the status is used instead.
  }
  const retryAfterSeconds = parseRetryAfter(response.headers.get('Retry-After'));
  if (isErrorBody(body)) {
    return new ApiError({
      status: response.status,
      code: body.error.code,
      serverMessage: body.error.message,
      retryAfterSeconds,
    });
  }
  return new ApiError({
    status: response.status,
    code: `http_${response.status}`,
    retryAfterSeconds,
  });
}
