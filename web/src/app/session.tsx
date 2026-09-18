import { createContext, useContext, type ReactNode } from 'react';

import type { Me } from '../api/types';

const SessionContext = createContext<Me | null>(null);

/** Provides the loaded, consented profile to every screen behind the session gate. */
export function SessionProvider({ me, children }: { me: Me; children: ReactNode }) {
  return <SessionContext.Provider value={me}>{children}</SessionContext.Provider>;
}

export function useSession(): Me {
  const me = useContext(SessionContext);
  if (!me) throw new Error('useSession must be used behind the session gate');
  return me;
}
