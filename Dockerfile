# syntax=docker/dockerfile:1
#
# One container: the SPA, the API and the run supervisor's Python runtime.
#
# `upstream/` is git-ignored (424 MB of reference checkouts), so a build from
# the Git repository cannot COPY it. The image fetches both official trees at
# the commits in source-lock.json instead; a moving branch would silently
# change the product.

# -- build the SPA ---------------------------------------------------------
FROM node:24-alpine AS build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# -- runtime ---------------------------------------------------------------
FROM python:3.12-slim AS runtime

ARG AGENT_CORE_COMMIT=6df9699e650b3ad9b9835b46ae3c3b7572adb04b
ARG JIUWENSWARM_COMMIT=3fea8d1623a13646e935df9e96100ab7ad470aa4

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKBENCH_ENV=production \
    WORKBENCH_DATA_DIR=/data \
    PYTHONPATH=/app/upstream/agent-core:/app/upstream/jiuwenswarm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY p0a/requirements-frozen.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /app/upstream \
    && curl -fsSL "https://github.com/openJiuwen-ai/agent-core/archive/${AGENT_CORE_COMMIT}.tar.gz" \
         | tar -xz -C /app/upstream \
    && mv "/app/upstream/agent-core-${AGENT_CORE_COMMIT}" /app/upstream/agent-core \
    && curl -fsSL "https://github.com/openJiuwen-ai/jiuwenswarm/archive/${JIUWENSWARM_COMMIT}.tar.gz" \
         | tar -xz -C /app/upstream \
    && mv "/app/upstream/jiuwenswarm-${JIUWENSWARM_COMMIT}" /app/upstream/jiuwenswarm

COPY app ./app
COPY scripts ./scripts
COPY corpus ./corpus
COPY source-lock.json ./source-lock.json
COPY --from=build /web/dist ./web/dist

RUN useradd --create-home --uid 10001 workbench \
    && mkdir -p /data \
    && chown -R workbench:workbench /data /app
USER workbench

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# One worker by design: the run registry, the concurrency cap and SQLite all
# live in this process. CloudBase must run exactly one instance.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
