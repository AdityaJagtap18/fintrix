/// <reference types="vite/client" />

interface ImportMetaEnv {
  // Baked in at build time when the frontend and backend are deployed on separate
  // subdomains (see src/web/Dockerfile and api/config.ts) — empty/unset in dev, where
  // Vite's own proxy (vite.config.ts) makes every /api/* call same-origin already.
  readonly VITE_API_BASE_URL?: string;
}
