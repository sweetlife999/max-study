import { MaxUI } from '@maxhub/max-ui';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { ApiProvider } from '../api/ApiProvider';
import type { ApiClient } from '../api/client';
import type { Bridge } from '../bridge/bridge';
import { BridgeProvider } from '../bridge/BridgeProvider';

export interface AppProvidersProps {
  bridge: Bridge;
  api: ApiClient;
  queryClient: QueryClient;
  children: ReactNode;
}

/**
 * Everything the screens need, without any routing — tests mount the same tree around a
 * MemoryRouter. `MaxUI` detects the platform and colour scheme on its own: MAX Bridge exposes
 * no theme API, so there is nothing to feed it.
 */
export function AppProviders({ bridge, api, queryClient, children }: AppProvidersProps) {
  return (
    <MaxUI>
      <BridgeProvider bridge={bridge}>
        <ApiProvider client={api}>
          <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
        </ApiProvider>
      </BridgeProvider>
    </MaxUI>
  );
}
