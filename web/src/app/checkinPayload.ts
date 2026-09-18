import { createContext, useContext } from 'react';

import type { CheckinPayload } from '../lib/checkinPayload';

export const CheckinPayloadContext = createContext<{
  payload: CheckinPayload | null;
  setPayload: (payload: CheckinPayload) => void;
} | null>(null);

/** Codes stay in React memory, never in the URL or browser history state. */
export function useCheckinPayload() {
  const context = useContext(CheckinPayloadContext);
  if (!context) throw new Error('Missing check-in provider');
  return context;
}
