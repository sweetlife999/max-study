import { createContext, useContext, useEffect, useRef, type ReactNode } from 'react';

import type { Bridge } from './bridge';

const BridgeContext = createContext<Bridge | null>(null);

export function BridgeProvider({ bridge, children }: { bridge: Bridge; children: ReactNode }) {
  return <BridgeContext.Provider value={bridge}>{children}</BridgeContext.Provider>;
}

export function useBridge(): Bridge {
  const bridge = useContext(BridgeContext);
  if (!bridge) throw new Error('useBridge must be used inside <BridgeProvider>');
  return bridge;
}

/** Binds the native MAX Back button to `onBack` while the calling screen is mounted. */
export function useNativeBackButton(onBack: (() => void) | null): void {
  const bridge = useBridge();
  const handlerRef = useRef(onBack);
  useEffect(() => {
    handlerRef.current = onBack;
  });
  const enabled = onBack !== null;
  useEffect(() => {
    if (!enabled) return undefined;
    return bridge.showBackButton(() => handlerRef.current?.());
  }, [bridge, enabled]);
}
