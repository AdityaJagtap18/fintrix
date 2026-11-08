FROM node:20-slim AS build
WORKDIR /app
# The frontend and backend deploy on separate subdomains (see docker-compose.prod.yml), so
# the browser's API calls need the backend's real origin baked in at build time — Vite
# only picks up VITE_* vars from an .env file it finds at build time, not from the
# shell/--build-arg directly, hence writing one here. Empty default = relative /api/*
# calls, correct for a same-origin (one-subdomain) deployment or local `npm run build`.
ARG VITE_API_BASE_URL=""
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN echo "VITE_API_BASE_URL=${VITE_API_BASE_URL}" > .env.production
RUN npm run build

FROM nginx:1.27-alpine
# The pinned base image's apk packages (openssl, curl, expat, libpng, ...) lag behind
# Alpine's own already-released security patches — pull those in without bumping the
# nginx/Alpine major version itself.
RUN apk update && apk upgrade --no-cache
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
