# syntax=docker/dockerfile:1

# Build context is web/ (from the repository root: `docker build -t campus-web:local web`).
# package.json, package-lock.json and Caddyfile are therefore at the context root.

# Node 24 LTS: the version pinned in web/.nvmrc and required by `engines` in package.json.
FROM node:24-alpine AS build
WORKDIR /app

# Dependencies first so edits to the sources reuse the cached install layer.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Baked into the bundle at build time. `/api` (the default) is what the Caddyfile proxies.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM caddy:2-alpine AS runtime
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
EXPOSE 80
# The base image already runs `caddy run --config /etc/caddy/Caddyfile --adapter caddyfile`.
