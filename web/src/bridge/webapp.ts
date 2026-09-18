/**
 * The subset of `window.WebApp` this app uses, typed after
 * https://dev.max.ru/docs/webapps/bridge (checked 2026-09-17) and the published script
 * https://st.max.ru/js/max-web-app.js.
 *
 * Every member is optional on purpose: the script may be missing (opened in a plain browser,
 * CDN blocked) or an older client may lack a method. Callers must feature-detect.
 */

export type MaxPlatform = 'ios' | 'android' | 'desktop' | 'web';

export interface WebAppInitDataUnsafe {
  query_id?: string;
  auth_date?: number;
  hash?: string;
  start_param?: string;
  user?: {
    id: number;
    first_name?: string;
    last_name?: string;
    username?: string;
    language_code?: string;
    photo_url?: string;
  };
  chat?: { id: number; type: 'DIALOG' | 'CHAT' | 'CHANNEL' };
}

/** Rejection payload of Bridge promises: `{ error: { code } }` (docs, "Ошибки и обработка"). */
export interface WebAppErrorPayload {
  error: { code: string };
}

export type HapticNotificationType = 'error' | 'success' | 'warning';

export interface WebAppShareTextParams {
  text?: string;
  link?: string;
}

export interface WebApp {
  initData?: string | null;
  initDataUnsafe?: WebAppInitDataUnsafe;
  platform?: MaxPlatform | null;
  version?: string | null;

  /** Present in max-web-app.js (posts `WebAppReady`), not described on the docs page. */
  ready?: () => void;

  /** Resolves with the recognised QR content; `fileSelect=false` allows only the camera. */
  openCodeReader?: (fileSelect?: boolean) => Promise<unknown>;

  openLink?: (url: string) => void;
  openMaxLink?: (url: string) => void;
  /** Native share sheet for text and links (`WebAppShare`). */
  shareContent?: (params: WebAppShareTextParams) => Promise<unknown>;

  requestScreenMaxBrightness?: () => Promise<unknown>;
  restoreScreenBrightness?: () => Promise<unknown>;

  BackButton?: {
    show: () => void;
    hide: () => void;
    onClick: (callback: () => void) => void;
    offClick: (callback: () => void) => void;
  };

  HapticFeedback?: {
    notificationOccurred: (
      type: HapticNotificationType,
      disableVibrationFallback?: boolean,
    ) => Promise<unknown>;
  };
}

declare global {
  interface Window {
    WebApp?: WebApp;
  }
}
