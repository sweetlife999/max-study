import { BrowserRouter } from 'react-router';

import { AppProviders, type AppProvidersProps } from './AppProviders';
import { AppRoutes } from './AppRoutes';
import { SessionGate } from './SessionGate';

export type AppProps = Omit<AppProvidersProps, 'children'>;

export function App(props: AppProps) {
  return (
    <AppProviders {...props}>
      <BrowserRouter>
        <SessionGate>
          <AppRoutes />
        </SessionGate>
      </BrowserRouter>
    </AppProviders>
  );
}
