import type { ReactNode } from 'react';

import type { Me } from '../api/types';
import { SessionContext } from './session';

/** Provides the loaded, consented profile to every screen behind the session gate. */
export function SessionProvider({ me, children }: { me: Me; children: ReactNode }) {
  return <SessionContext.Provider value={me}>{children}</SessionContext.Provider>;
}
