import { delay, http } from 'msw';
import { setupWorker } from 'msw/browser';

import { handlers } from './handlers';

/** Adds a small latency so loading states are visible during `npm run dev:mock`. */
const latency = http.all('*/api/*', async () => {
  await delay(350);
});

export const worker = setupWorker(latency, ...handlers);
