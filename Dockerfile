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
COPY --from=ui /ui/dist src/memo/static/ui
RUN pip install --no-cache-dir . fastembed uvicorn fastapi
RUN python -c "import memo, os, pathlib; os.symlink(pathlib.Path(memo.__file__).parent / 'static' / 'ui', '/app/ui')"

RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

ENV HF_HUB_DISABLE_SYMLINKS=1
RUN python -c "from fastembed import TextEmbedding; \
    TextEmbedding('sentence-transformers/all-MiniLM-L6-v2', cache_dir='/opt/memo/models')"

COPY docker/entrypoint.sh docker/nginx.conf docker/healthcheck.sh /opt/memo/docker/
RUN chmod +x /opt/memo/docker/entrypoint.sh /opt/memo/docker/healthcheck.sh \
    && sed -i 's/\r$//' /opt/memo/docker/entrypoint.sh /opt/memo/docker/healthcheck.sh

VOLUME /data
EXPOSE 8080 5000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD /opt/memo/docker/healthcheck.sh
ENTRYPOINT ["/opt/memo/docker/entrypoint.sh"]
CMD ["all"]
