import { isApiError } from '../api/errors';
import type { Translate } from '../i18n/i18n';

/** A user-facing sentence for any error thrown by the API client or elsewhere. */
export function describeError(error: unknown, t: Translate): string {
  if (!isApiError(error)) return t('error.generic');
  if (error.isNetwork) return t('error.network');
  if (error.isRateLimited) {
    return error.retryAfterSeconds
      ? t('error.rateLimitedWait', { minutes: Math.max(1, Math.ceil(error.retryAfterSeconds / 60)) })
      : t('error.rateLimited');
  }
  if (error.isUnauthorized) return t('error.unauthorized');
  if (error.serverMessage) return error.serverMessage;
  if (error.status === 404) return t('error.notFound');
  if (error.status === 403) return t('error.forbidden');
  return t('error.generic');
}
