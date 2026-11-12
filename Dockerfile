# syntax=docker/dockerfile:1
# ---------------------------------------------------------------- backend
FROM python:3.12-slim AS backend

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ffmpeg brings ffprobe: the bundled imageio-ffmpeg wheel is a fallback for hosts
# without a system package, but a real deployment should have both binaries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential libpq-dev ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY meiko ./meiko
COPY migrations ./migrations
COPY alembic.ini start.py ./
COPY scripts ./scripts

ENV DATA_DIR=/data
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python start.py --service health || exit 1

# Default: the combined launcher stays compatible with `python start.py`.
CMD ["python", "start.py"]

# ---------------------------------------------------------------- webapp
FROM node:22-alpine AS webapp-deps
WORKDIR /app
COPY webapp/package.json webapp/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

FROM node:22-alpine AS webapp-build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=webapp-deps /app/node_modules ./node_modules
COPY webapp ./
RUN npm run build

FROM node:22-alpine AS webapp
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=webapp-build /app/.next/standalone ./
COPY --from=webapp-build /app/.next/static ./.next/static
COPY --from=webapp-build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
