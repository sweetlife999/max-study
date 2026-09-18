import { createContext, useContext } from 'react';

import type { Me } from '../api/types';

export const SessionContext = createContext<Me | null>(null);

export function useSession(): Me {
  const me = useContext(SessionContext);
  if (!me) throw new Error('useSession must be used behind the session gate');
  return me;
}
