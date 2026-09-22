import { createContext, useContext } from 'react';

export const ConsentDuringLaunchContext = createContext(false);

export function useConsentDuringLaunch() {
  return useContext(ConsentDuringLaunchContext);
}
