import type { ReactNode } from 'react';

import type { Bridge } from './bridge';
import { BridgeContext } from './context';

export function BridgeProvider({ bridge, children }: { bridge: Bridge; children: ReactNode }) {
  return <BridgeContext.Provider value={bridge}>{children}</BridgeContext.Provider>;
}
