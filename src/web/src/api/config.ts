// In dev, Vite's own proxy (vite.config.ts) forwards /api/* to localhost:8000, so this
// stays empty and every request below is same-origin relative. In production, when the
// frontend and backend are deployed on separate subdomains, VITE_API_BASE_URL is baked in
// at build time (see src/web/Dockerfile's VITE_API_BASE_URL build arg) and prefixes every
// request with the backend's real origin.
export const API_ORIGIN = import.meta.env.VITE_API_BASE_URL ?? "";
