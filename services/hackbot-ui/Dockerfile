# syntax=docker/dockerfile:1

FROM node:26-slim AS deps
WORKDIR /app
COPY services/hackbot-ui/package.json services/hackbot-ui/package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:26-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY services/hackbot-ui/ .

ARG SENTRY_DSN
ARG SENTRY_RELEASE
ENV NEXT_PUBLIC_SENTRY_DSN=$SENTRY_DSN \
    SENTRY_RELEASE=$SENTRY_RELEASE

ENV NEXT_TELEMETRY_DISABLED=1

# Un-silences the Sentry build plugin, so a failed source-map upload shows up in
# the build log (see `silent` in next.config.mjs).
ENV CI=true

RUN --mount=type=secret,id=sentry_auth_token \
    SENTRY_AUTH_TOKEN="$(cat /run/secrets/sentry_auth_token 2>/dev/null || true)" \
    npm run build

FROM node:26-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
# Cloud Run injects its own PORT (8080) which the standalone server honors.
# Bind to all interfaces — the standalone server defaults HOSTNAME to
# "localhost", which would fail Cloud Run's health check.
ENV HOSTNAME=0.0.0.0

RUN useradd --create-home --shell /bin/bash app

# Next.js standalone output bundles only the files needed to run the server.
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

USER app
EXPOSE 3000
CMD ["node", "server.js"]
