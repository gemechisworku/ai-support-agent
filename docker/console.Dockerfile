# The Next.js operations console (web/console, specs/11-console). Build context: the repo root.
#   docker compose build console      (compose service `console`, 127.0.0.1:8501)
# Stages: deps (cached on package-lock.json) → build (next build, standalone output) → runner (non-root).
# Runtime config is server-side only: CONSOLE_API_URL and CONSOLE_API_TOKEN (never baked into the image).

FROM node:24-bookworm-slim AS deps
WORKDIR /app
COPY web/console/package.json web/console/package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:24-bookworm-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY web/console/ ./
RUN npm run build

FROM node:24-bookworm-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=8501 \
    HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 8501
CMD ["node", "server.js"]
