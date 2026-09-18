import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// `npm run dev:mock` runs Vite in "mock" mode: MSW intercepts /api in the browser and a fake
// MAX Bridge is installed. The MSW service worker lives in `mock-public/`, so it never ends up
// in the production bundle.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  publicDir: mode === 'mock' ? 'mock-public' : 'public',
  server: {
    port: 5173,
    proxy:
      mode === 'mock'
        ? undefined
        : { '/api': { target: process.env.API_PROXY_TARGET ?? 'http://localhost:8000' } },
  },
  build: {
    target: 'es2022',
    sourcemap: false,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    restoreMocks: true,
    unstubGlobals: true,
    unstubEnvs: true,
  },
}));
