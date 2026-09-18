import type { Bridge, ScanOutcome, ShareOutcome } from './bridge';
import type { HapticNotificationType, MaxPlatform } from './webapp';

export interface FakeBridgeOptions {
  isInMax?: boolean;
  initData?: string;
  startParam?: string | null;
  platform?: MaxPlatform | null;
  languageCode?: string | null;
  canScanQr?: boolean;
  /** Called on every scan; defaults to "cancelled". */
  scan?: () => Promise<ScanOutcome>;
  shareOutcome?: ShareOutcome;
}

/** In-memory Bridge for tests and `npm run dev:mock`. Records calls for assertions. */
export class FakeBridge implements Bridge {
  readonly isInMax: boolean;
  readonly initData: string;
  readonly startParam: string | null;
  readonly platform: MaxPlatform | null;
  readonly languageCode: string | null;
  readonly canScanQr: boolean;

  readyCalls = 0;
  scanCalls = 0;
  brightnessRequested = false;
  readonly notifications: HapticNotificationType[] = [];
  readonly shares: { text: string; link?: string }[] = [];
  private backHandler: (() => void) | null = null;
  private readonly scanImpl: () => Promise<ScanOutcome>;
  private readonly shareOutcome: ShareOutcome;

  constructor(options: FakeBridgeOptions = {}) {
    this.isInMax = options.isInMax ?? true;
    this.initData = options.initData ?? (this.isInMax ? 'fake-init-data' : '');
    this.startParam = options.startParam ?? null;
    this.platform = options.platform ?? 'android';
    this.languageCode = options.languageCode ?? 'ru';
    this.canScanQr = options.canScanQr ?? false;
    this.scanImpl = options.scan ?? (() => Promise.resolve({ status: 'cancelled' }));
    this.shareOutcome = options.shareOutcome ?? 'shared';
  }

  get isBackButtonVisible(): boolean {
    return this.backHandler !== null;
  }

  pressBack(): void {
    this.backHandler?.();
  }

  ready(): void {
    this.readyCalls += 1;
  }

  scanQr(): Promise<ScanOutcome> {
    this.scanCalls += 1;
    return this.scanImpl();
  }

  showBackButton(handler: () => void): () => void {
    this.backHandler = handler;
    return () => {
      if (this.backHandler === handler) this.backHandler = null;
    };
  }

  notify(type: HapticNotificationType): void {
    this.notifications.push(type);
  }

  share(params: { text: string; link?: string }): Promise<ShareOutcome> {
    this.shares.push(params);
    return Promise.resolve(this.shareOutcome);
  }

  requestMaxBrightness(): void {
    this.brightnessRequested = true;
  }

  restoreBrightness(): void {
    this.brightnessRequested = false;
  }
}
