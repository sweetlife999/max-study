import { render, type RenderResult } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useEffect } from 'react';
import { MemoryRouter, useLocation } from 'react-router';

import { createApiClient, DEFAULT_API_BASE_URL } from '../api/client';
import { createQueryClient } from '../api/queries';
import { AppProviders } from '../app/AppProviders';
import { AppRoutes } from '../app/AppRoutes';
import { SessionGate } from '../app/SessionGate';
import { FakeBridge, type FakeBridgeOptions } from '../bridge/fake';

export interface RenderAppOptions {
  /** Initial route; defaults to the main screen. */
  route?: string;
  onLocation?: (location: ReturnType<typeof useLocation>) => void;
  bridge?: FakeBridge;
  bridgeOptions?: FakeBridgeOptions;
  /** Set when the test installs `vi.useFakeTimers()`, so user-event can drive them. */
  fakeTimers?: boolean;
}

export interface RenderedApp extends RenderResult {
  bridge: FakeBridge;
  user: ReturnType<typeof userEvent.setup>;
}

/** Mounts the whole app (providers, session gate, routes) against the MSW mock backend. */
export function renderApp(options: RenderAppOptions = {}): RenderedApp {
  const bridge = options.bridge ?? new FakeBridge(options.bridgeOptions);
  const api = createApiClient({
    baseUrl: DEFAULT_API_BASE_URL,
    getInitData: () => bridge.initData,
  });
  const user = userEvent.setup(
    options.fakeTimers ? { advanceTimers: (ms) => void vi.advanceTimersByTime(ms) } : {},
  );

  const result = render(
    <AppProviders bridge={bridge} api={api} queryClient={createQueryClient()}>
      <MemoryRouter initialEntries={[options.route ?? '/']}>
        <LocationProbe onLocation={options.onLocation} />
        <SessionGate>
          <AppRoutes />
        </SessionGate>
      </MemoryRouter>
    </AppProviders>,
  );

  return { ...result, bridge, user };
}

function LocationProbe({ onLocation }: { onLocation: RenderAppOptions['onLocation'] }) {
  const location = useLocation();
  useEffect(() => {
    onLocation?.(location);
  }, [location, onLocation]);
  return null;
}
