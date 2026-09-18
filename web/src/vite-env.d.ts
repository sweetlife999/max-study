/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the campus API. Defaults to `/api`, which Caddy proxies to the backend. */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
