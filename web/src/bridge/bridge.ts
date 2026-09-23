import type {
  HapticNotificationType,
  MaxPlatform,
  WebApp,
  WebAppErrorPayload,
  WebAppShareTextParams,
} from './webapp';

export type ScanOutcome =
  { status: 'scanned'; text: string } | { status: 'cancelled' } | { status: 'unavailable' };

export type ShareOutcome = 'shared' | 'failed' | 'unavailable';

/** App-facing, promise-safe wrapper around MAX Bridge. Never throws. */
export interface Bridge {
  /** True when launched by a MAX client, i.e. signed `initData` is present. */
  readonly isInMax: boolean;
  /** Raw `initData` for the `X-Max-Init-Data` header; empty outside MAX. */
  readonly initData: string;
  readonly startParam: string | null;
  readonly platform: MaxPlatform | null;
  /** `initDataUnsafe.user.language_code` — only a hint until `/api/me` answers. */
  readonly languageCode: string | null;
  readonly canScanQr: boolean;

  ready(): void;
  /** Closes the miniapp when supported; never throws. */
  close(): void;
  scanQr(): Promise<ScanOutcome>;
  /** Shows the native Back button bound to `handler`; the returned function removes it. */
  showBackButton(handler: () => void): () => void;
  notify(type: HapticNotificationType): void;
  /** Must be called synchronously from a click handler: MAX checks for a user gesture. */
  share(params: { text: string; link?: string }): Promise<ShareOutcome>;
  requestMaxBrightness(): void;
  restoreBrightness(): void;
}

/** Documented deeplink that opens the "Send to MAX" screen (dev.max.ru/docs/webapps/introduction). */
export function buildShareDeeplink(text: string): string {
  return `https://max.ru/:share?text=${encodeURIComponent(text)}`;
}

/**
 * The docs say `openCodeReader` returns "a string", while max-web-app.js resolves with the
 * response payload object. Accept both; for objects prefer a `value`-like field.
 */
export function normalizeScanValue(value: unknown): string | null {
  if (typeof value === 'string') return value;
  if (typeof value !== 'object' || value === null) return null;
  const record = value as Record<string, unknown>;
  for (const key of ['value', 'text', 'data', 'result', 'code']) {
    const candidate = record[key];
    if (typeof candidate === 'string') return candidate;
  }
  const strings = Object.values(record).filter((v): v is string => typeof v === 'string');
  return strings.length === 1 ? (strings[0] ?? null) : null;
}

function errorCodeOf(reason: unknown): string {
  if (typeof reason !== 'object' || reason === null || !('error' in reason)) return '';
  const { error } = reason as Partial<WebAppErrorPayload>;
  return typeof error?.code === 'string' ? error.code : '';
}

function ignoreRejection(promise: Promise<unknown> | undefined): void {
  promise?.catch(() => undefined);
}

/** Platforms where the docs state HapticFeedback is not supported. */
const NO_HAPTICS: readonly MaxPlatform[] = ['desktop', 'web'];

export function createMaxBridge(webApp: WebApp | undefined, search: string): Bridge {
  const initData = typeof webApp?.initData === 'string' ? webApp.initData : '';
  const isInMax = initData.length > 0;
  const unsafe = webApp?.initDataUnsafe;
  // Documented source: `initDataUnsafe.start_param` (a string, per the Bridge reference — the
  // introduction page calls it a "WebAppStartParam object", which the shipped script contradicts).
  // The `?startapp=` fallback is NOT documented; it only makes deeplinks testable in a browser,
  // where `initData` is absent anyway.
  const startParam =
    (typeof unsafe?.start_param === 'string' && unsafe.start_param) ||
    new URLSearchParams(search).get('startapp') ||
    null;
  const platform = webApp?.platform ?? null;
  const openCodeReader =
    isInMax && webApp !== undefined && typeof webApp.openCodeReader === 'function'
      ? webApp.openCodeReader.bind(webApp)
      : null;
  const canScanQr = openCodeReader !== null;

  return {
    isInMax,
    initData,
    startParam,
    platform,
    languageCode: unsafe?.user?.language_code ?? null,
    canScanQr,

    ready() {
      try {
        webApp?.ready?.();
      } catch {
        // The app works without the ready signal.
      }
    },

    close() {
      if (!isInMax) return;
      try {
        webApp?.close?.();
      } catch {
        // Closing is best-effort; the app remains usable if the client rejects it.
      }
    },

    async scanQr() {
      if (openCodeReader === null) return { status: 'unavailable' };
      try {
        // fileSelect=false: camera only, picking an image from the gallery is not allowed.
        const text = normalizeScanValue(await openCodeReader(false));
        return text ? { status: 'scanned', text } : { status: 'cancelled' };
      } catch (reason) {
        return errorCodeOf(reason).includes('cancel')
          ? { status: 'cancelled' }
          : { status: 'unavailable' };
      }
    },

    showBackButton(handler) {
      const backButton = isInMax ? webApp?.BackButton : undefined;
      if (!backButton) return () => undefined;
      try {
        backButton.onClick(handler);
        backButton.show();
      } catch {
        return () => undefined;
      }
      return () => {
        try {
          backButton.offClick(handler);
          backButton.hide();
        } catch {
          // Nothing to clean up if the client rejected the call.
        }
      };
    },

    notify(type) {
      if (!isInMax || (platform !== null && NO_HAPTICS.includes(platform))) return;
      try {
        ignoreRejection(webApp?.HapticFeedback?.notificationOccurred(type));
      } catch {
        // Haptics are decorative.
      }
    },

    async share({ text, link }) {
      if (!isInMax) return 'unavailable';
      try {
        // `shareContent` is the documented text/link share ("Контакты и шеринг"); in
        // max-web-app.js it posts `WebAppShare` with the params untouched. `shareMaxContent`
        // is for forwarding a message the bot already sent (it expects `mid`/`chatType`), so
        // it is deliberately not used here.
        if (typeof webApp?.shareContent === 'function') {
          const params: WebAppShareTextParams = link ? { text, link } : { text };
          await webApp.shareContent(params);
          return 'shared';
        }
        // Older clients without `shareContent`: the documented `:share` deeplink.
        if (typeof webApp?.openMaxLink === 'function') {
          webApp.openMaxLink(buildShareDeeplink(link ? `${text}\n${link}` : text));
          return 'shared';
        }
      } catch {
        return 'failed';
      }
      return 'unavailable';
    },

    requestMaxBrightness() {
      if (!isInMax) return;
      try {
        ignoreRejection(webApp?.requestScreenMaxBrightness?.());
      } catch {
        // Optional nicety for the QR screen.
      }
    },

    restoreBrightness() {
      if (!isInMax) return;
      try {
        ignoreRejection(webApp?.restoreScreenBrightness?.());
      } catch {
        // Optional nicety for the QR screen.
      }
    },
  };
}
