import '@maxhub/max-ui/dist/styles.css';
import './styles.css';

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { createApiClient, DEFAULT_API_BASE_URL } from './api/client';
import { createQueryClient } from './api/queries';
import { App } from './app/App';
import type { Bridge } from './bridge/bridge';
import { createMaxBridge } from './bridge/bridge';

const IS_MOCK = import.meta.env.MODE === 'mock';

/**
 * Outside MAX (`window.WebApp` missing, CDN blocked, plain browser) `createMaxBridge` still
 * returns a working object: no scanner, no back button, no signed init data. Nothing throws.
 */
async function createBridge(): Promise<Bridge> {
  if (!IS_MOCK) return createMaxBridge(window.WebApp, window.location.search);
  const [{ FakeBridge }, { worker }] = await Promise.all([
    import('./bridge/fake'),
    import('./mocks/browser'),
  ]);
  await worker.start({ onUnhandledRequest: 'bypass' });
  const search = new URLSearchParams(window.location.search);
  return new FakeBridge({
    startParam: search.get('startapp'),
    canScanQr: true,
    // `window.prompt` stands in for the native code reader while developing in a browser.
    scan: () => {
      const text = window.prompt('QR content (deeplink or ci_<event>_<code>)');
      return Promise.resolve(text ? { status: 'scanned', text } : { status: 'cancelled' });
    },
  });
}

const container = document.getElementById('root');
if (!container) throw new Error('#root is missing from index.html');
const root = createRoot(container);

void createBridge().then((bridge) => {
  const api = createApiClient({
    baseUrl: import.meta.env.VITE_API_BASE_URL ?? DEFAULT_API_BASE_URL,
    getInitData: () => bridge.initData,
  });
  root.render(
    <StrictMode>
      <App bridge={bridge} api={api} queryClient={createQueryClient()} />
    </StrictMode>,
  );
});
