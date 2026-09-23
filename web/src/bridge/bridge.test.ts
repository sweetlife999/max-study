import { buildShareDeeplink, createMaxBridge, normalizeScanValue } from './bridge';
import type { WebApp } from './webapp';

/** A `window.WebApp` stub: every member is optional, so each test supplies only what it needs. */
function webApp(overrides: Partial<WebApp> = {}): WebApp {
  return { initData: 'signed-init-data', platform: 'android', ...overrides };
}

/**
 * A Bridge call that fails. MAX rejects with a plain `{ error: { code } }` payload rather than
 * an `Error` (docs, "Ошибки и обработка"; `requestController.createRequest` in max-web-app.js),
 * so the fixtures have to reject with one too — hence the single lint exemption below.
 */
function rejectWithBridgeError(code: string): Promise<never> {
  // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
  return Promise.reject({ error: { code } });
}

describe('createMaxBridge outside MAX', () => {
  it('reports every capability as absent instead of throwing', async () => {
    const bridge = createMaxBridge(undefined, '');

    expect(bridge.isInMax).toBe(false);
    expect(bridge.initData).toBe('');
    expect(bridge.canScanQr).toBe(false);
    expect(bridge.platform).toBeNull();
    expect(bridge.languageCode).toBeNull();
    await expect(bridge.scanQr()).resolves.toEqual({ status: 'unavailable' });
    await expect(bridge.share({ text: 'hi' })).resolves.toBe('unavailable');
    expect(() => bridge.ready()).not.toThrow();
    expect(() => bridge.notify('success')).not.toThrow();
    expect(() => bridge.close()).not.toThrow();
    expect(() => bridge.requestMaxBrightness()).not.toThrow();
    expect(() => bridge.restoreBrightness()).not.toThrow();
    expect(() => bridge.showBackButton(() => undefined)()).not.toThrow();
  });

  it('treats an empty initData as "not launched by a MAX client"', () => {
    expect(createMaxBridge(webApp({ initData: '' }), '').isInMax).toBe(false);
    expect(createMaxBridge(webApp({ initData: null }), '').isInMax).toBe(false);
  });
});

describe('close', () => {
  it('closes an in-MAX app', () => {
    const close = vi.fn();

    createMaxBridge(webApp({ close }), '').close();

    expect(close).toHaveBeenCalledTimes(1);
  });

  it('does not call the client outside MAX', () => {
    const close = vi.fn();

    createMaxBridge(webApp({ initData: '', close }), '').close();

    expect(close).not.toHaveBeenCalled();
  });

  it('survives a client that rejects the close call', () => {
    const close = vi.fn(() => {
      throw new Error('unsupported');
    });

    expect(() => createMaxBridge(webApp({ close }), '').close()).not.toThrow();
  });
});

describe('the start param', () => {
  it('comes from initDataUnsafe.start_param', () => {
    const app = webApp({ initDataUnsafe: { start_param: 'ci_7_123456' } });
    expect(createMaxBridge(app, '').startParam).toBe('ci_7_123456');
  });

  it('prefers initDataUnsafe over the ?startapp query parameter', () => {
    const app = webApp({ initDataUnsafe: { start_param: 'ci_7_123456' } });
    expect(createMaxBridge(app, '?startapp=ci_9_999999').startParam).toBe('ci_7_123456');
  });

  it('falls back to ?startapp so deeplinks can be opened in a plain browser', () => {
    expect(createMaxBridge(webApp(), '?startapp=ci_9_999999').startParam).toBe('ci_9_999999');
  });

  it('is null when neither source carries one', () => {
    expect(createMaxBridge(webApp(), '').startParam).toBeNull();
    expect(
      createMaxBridge(webApp({ initDataUnsafe: { start_param: '' } }), '').startParam,
    ).toBeNull();
  });
});

describe('ready', () => {
  it('signals the client once', () => {
    const ready = vi.fn();
    createMaxBridge(webApp({ ready }), '').ready();
    expect(ready).toHaveBeenCalledTimes(1);
  });

  it('survives a client that rejects the call', () => {
    const ready = vi.fn(() => {
      throw new Error('no transport');
    });
    expect(() => createMaxBridge(webApp({ ready }), '').ready()).not.toThrow();
  });
});

describe('scanQr', () => {
  it('opens the code reader with file selection disabled', async () => {
    const openCodeReader = vi.fn(() => Promise.resolve('ci_1_123456'));
    const bridge = createMaxBridge(webApp({ openCodeReader }), '');

    expect(bridge.canScanQr).toBe(true);
    await expect(bridge.scanQr()).resolves.toEqual({ status: 'scanned', text: 'ci_1_123456' });
    // Picking an image from the gallery is forbidden (§9): the argument must be exactly `false`.
    expect(openCodeReader).toHaveBeenCalledWith(false);
  });

  it('accepts the response object the shipped script actually resolves with', async () => {
    const openCodeReader = vi.fn(() => Promise.resolve({ value: 'ci_1_123456' }));
    const bridge = createMaxBridge(webApp({ openCodeReader }), '');
    await expect(bridge.scanQr()).resolves.toEqual({ status: 'scanned', text: 'ci_1_123456' });
  });

  it('reports a cancelled scan so the screen stays where it is', async () => {
    const openCodeReader = () => rejectWithBridgeError('client.web_app_open_code_reader.cancelled');
    await expect(createMaxBridge(webApp({ openCodeReader }), '').scanQr()).resolves.toEqual({
      status: 'cancelled',
    });
  });

  it('reports an empty result as cancelled, not as a scan of nothing', async () => {
    const openCodeReader = () => Promise.resolve({});
    await expect(createMaxBridge(webApp({ openCodeReader }), '').scanQr()).resolves.toEqual({
      status: 'cancelled',
    });
  });

  it('falls back to manual entry on any other failure', async () => {
    const openCodeReader = () =>
      rejectWithBridgeError('client.web_app_open_code_reader.request_timeout');
    await expect(createMaxBridge(webApp({ openCodeReader }), '').scanQr()).resolves.toEqual({
      status: 'unavailable',
    });
  });

  it('is unavailable outside MAX even when the method exists', async () => {
    const openCodeReader = vi.fn(() => Promise.resolve('ci_1_123456'));
    const bridge = createMaxBridge(webApp({ initData: '', openCodeReader }), '');

    expect(bridge.canScanQr).toBe(false);
    await expect(bridge.scanQr()).resolves.toEqual({ status: 'unavailable' });
    expect(openCodeReader).not.toHaveBeenCalled();
  });
});

describe('normalizeScanValue', () => {
  it.each([
    ['ci_1_123456', 'ci_1_123456'],
    [{ value: 'a' }, 'a'],
    [{ text: 'b' }, 'b'],
    [{ data: 'c' }, 'c'],
    [{ result: 'd' }, 'd'],
    [{ code: 'e' }, 'e'],
    [{ somethingElse: 'lonely' }, 'lonely'],
  ])('reads %o as %s', (input, expected) => {
    expect(normalizeScanValue(input)).toBe(expected);
  });

  it.each([[null], [undefined], [42], [{}], [{ a: 'one', b: 'two' }]])(
    'returns null for %o',
    (input) => {
      expect(normalizeScanValue(input)).toBeNull();
    },
  );
});

describe('the native back button', () => {
  function backButtonStub() {
    return { show: vi.fn(), hide: vi.fn(), onClick: vi.fn(), offClick: vi.fn() };
  }

  it('is shown while bound and removed on cleanup', () => {
    const BackButton = backButtonStub();
    const handler = () => undefined;
    const remove = createMaxBridge(webApp({ BackButton }), '').showBackButton(handler);

    expect(BackButton.onClick).toHaveBeenCalledWith(handler);
    expect(BackButton.show).toHaveBeenCalledTimes(1);

    remove();
    expect(BackButton.offClick).toHaveBeenCalledWith(handler);
    expect(BackButton.hide).toHaveBeenCalledTimes(1);
  });

  it('degrades to a no-op when the client has no back button', () => {
    const remove = createMaxBridge(webApp(), '').showBackButton(() => undefined);
    expect(() => remove()).not.toThrow();
  });

  it('degrades to a no-op when the client rejects the call', () => {
    const BackButton = backButtonStub();
    BackButton.show.mockImplementation(() => {
      throw new Error('unsupported');
    });
    const remove = createMaxBridge(webApp({ BackButton }), '').showBackButton(() => undefined);

    expect(() => remove()).not.toThrow();
    expect(BackButton.hide).not.toHaveBeenCalled();
  });
});

describe('share', () => {
  it('uses the documented text/link share', async () => {
    const shareContent = vi.fn(() => Promise.resolve(undefined));
    const bridge = createMaxBridge(webApp({ shareContent }), '');

    await expect(bridge.share({ text: 'Join', link: 'https://max.ru/bot' })).resolves.toBe(
      'shared',
    );
    expect(shareContent).toHaveBeenCalledWith({ text: 'Join', link: 'https://max.ru/bot' });
  });

  it('omits an absent link instead of sending undefined', async () => {
    const shareContent = vi.fn(() => Promise.resolve(undefined));
    await createMaxBridge(webApp({ shareContent }), '').share({ text: 'Join' });
    expect(shareContent).toHaveBeenCalledWith({ text: 'Join' });
  });

  it('falls back to the :share deeplink on a client without shareContent', async () => {
    const openMaxLink = vi.fn();
    const bridge = createMaxBridge(webApp({ openMaxLink }), '');

    await expect(bridge.share({ text: 'Join', link: 'https://max.ru/bot' })).resolves.toBe(
      'shared',
    );
    expect(openMaxLink).toHaveBeenCalledWith(buildShareDeeplink('Join\nhttps://max.ru/bot'));
  });

  it('reports a rejected share as failed so the caller can offer copying', async () => {
    const shareContent = () => rejectWithBridgeError('client.web_app_share.request_timeout');
    await expect(
      createMaxBridge(webApp({ shareContent }), '').share({ text: 'Join' }),
    ).resolves.toBe('failed');
  });

  it('reports a client with no share method at all as unavailable', async () => {
    await expect(createMaxBridge(webApp(), '').share({ text: 'Join' })).resolves.toBe(
      'unavailable',
    );
  });
});

describe('buildShareDeeplink', () => {
  it('percent-encodes the text into the documented :share deeplink', () => {
    expect(buildShareDeeplink('a b&c')).toBe('https://max.ru/:share?text=a%20b%26c');
  });
});

describe('haptics', () => {
  it('fire on platforms that support them', () => {
    const notificationOccurred = vi.fn(() => Promise.resolve(undefined));
    createMaxBridge(
      webApp({ platform: 'ios', HapticFeedback: { notificationOccurred } }),
      '',
    ).notify('success');
    expect(notificationOccurred).toHaveBeenCalledWith('success');
  });

  it.each([['desktop'], ['web']] as const)(
    'are skipped on %s, where the docs say they are not supported',
    (platform) => {
      const notificationOccurred = vi.fn(() => Promise.resolve(undefined));
      createMaxBridge(webApp({ platform, HapticFeedback: { notificationOccurred } }), '').notify(
        'error',
      );
      expect(notificationOccurred).not.toHaveBeenCalled();
    },
  );

  it('never surface a rejected haptic promise', () => {
    const notificationOccurred = vi.fn(() => rejectWithBridgeError('client.x.unsupported'));
    expect(() =>
      createMaxBridge(webApp({ HapticFeedback: { notificationOccurred } }), '').notify('warning'),
    ).not.toThrow();
  });
});

describe('screen brightness', () => {
  it('is raised and restored around the QR screen', () => {
    const requestScreenMaxBrightness = vi.fn(() => Promise.resolve(undefined));
    const restoreScreenBrightness = vi.fn(() => Promise.resolve(undefined));
    const bridge = createMaxBridge(
      webApp({ requestScreenMaxBrightness, restoreScreenBrightness }),
      '',
    );

    bridge.requestMaxBrightness();
    bridge.restoreBrightness();

    expect(requestScreenMaxBrightness).toHaveBeenCalledTimes(1);
    expect(restoreScreenBrightness).toHaveBeenCalledTimes(1);
  });

  it('never surfaces a rejected brightness promise', () => {
    const requestScreenMaxBrightness = vi.fn(() => rejectWithBridgeError('client.x.denied'));
    expect(() =>
      createMaxBridge(webApp({ requestScreenMaxBrightness }), '').requestMaxBrightness(),
    ).not.toThrow();
  });
});
