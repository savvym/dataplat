# docker/web/Dockerfile
# Multi-stage build for the React/Vite SPA (F-055).
#
# Build context: repo root (set in docker-compose.dev.yml as context: ..)
# Stage 1 (builder): Node 22 alpine — installs deps and runs Vite build.
# Stage 2 (runtime): nginx 1.27 alpine — serves the built dist/ directory.

# ── Stage 1: builder ─────────────────────────────────────────────────────
FROM node:22-alpine AS builder

# Build argument: baked into the static bundle at build time via Vite's
# import.meta.env mechanism. Default matches fastapi host port mapping.
ARG VITE_API_BASE_URL=http://localhost:18000
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

WORKDIR /repo

# Enable corepack and pin pnpm to the exact version used for the lockfile.
# (Avoids corepack downloading the latest pnpm, which may have stricter supply-chain
# policies that reject recently-published packages in the lockfile.)
RUN corepack enable && corepack prepare pnpm@9.15.4 --activate

# Copy workspace descriptor, lockfile, and app package files.
COPY pnpm-workspace.yaml pnpm-lock.yaml ./
# Copy root package.json if present (workspace root descriptor).
COPY package.json* ./

# Copy the web app source.
COPY apps/web ./apps/web

# Install dependencies (frozen lockfile for reproducibility).
RUN pnpm install --frozen-lockfile

# Build the production bundle.
RUN pnpm --filter web build

# ── Stage 2: runtime ──────────────────────────────────────────────────────
FROM nginx:1.27-alpine

# Copy the built SPA assets.
COPY --from=builder /repo/apps/web/dist /usr/share/nginx/html

# Copy nginx configuration (SPA routing via try_files).
COPY docker/web/nginx.conf /etc/nginx/conf.d/default.conf
