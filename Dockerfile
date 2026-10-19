# memo v3 - one image, three launchables, one public port.
#
#   docker run <image>          memo server on 127.0.0.1:5000 (internal) + nginx on :8080 (public)
#   docker run <image> server   memo server only, on 0.0.0.0:5000
#   docker run <image> ui       nginx only, on :8080 (proxying the API to $MEMO_API)
#
# The dashboard is built in a throwaway node stage and lands in /app/ui; the
# python server serves the same files (MEMO_UI_DIR), so a direct hit on the
# python port still shows the dashboard. The embedding weights are baked into
# /opt/memo/models, so a plain `docker run` needs no mount; a /models bind
# mount still wins when it is present.

FROM node:22-slim AS ui
WORKDIR /ui
COPY ui/package*.json ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; \
    else npm install --no-audit --no-fund; fi
COPY ui/ ./
RUN npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir . fastembed uvicorn

# nginx is the public face: it serves the dashboard and proxies the API.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

COPY --from=ui /ui/dist /app/ui
ENV MEMO_UI_DIR=/app/ui

# Bake the embedding weights: the image runs without any volume. A /models
# bind mount still wins at runtime (see src/memo/embeddings.py).
ENV HF_HUB_DISABLE_SYMLINKS=1
RUN python -c "from fastembed import TextEmbedding; \
    TextEmbedding('sentence-transformers/all-MiniLM-L6-v2', cache_dir='/opt/memo/models')"

COPY docker/entrypoint.sh docker/nginx.conf docker/healthcheck.sh /opt/memo/docker/
# sed: a CRLF checkout (core.autocrlf on Windows) would break the shebang.
RUN chmod +x /opt/memo/docker/entrypoint.sh /opt/memo/docker/healthcheck.sh \
    && sed -i 's/\r$//' /opt/memo/docker/entrypoint.sh /opt/memo/docker/healthcheck.sh

VOLUME /data
EXPOSE 8080 5000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD /opt/memo/docker/healthcheck.sh
ENTRYPOINT ["/opt/memo/docker/entrypoint.sh"]
CMD ["all"]
