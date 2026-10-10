# memo v3 — single container-first server (spec §4). Model baked at build
# time so the offline-download failure (F-01) can never happen inside.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/root/.cache/fastembed

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir . fastembed uvicorn \
 && python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')" \
 || (echo "MODEL BAKE FAILED — refusing image without embeddings" >&2; exit 1)

VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -m memo.server --health http://127.0.0.1:8000/health || exit 1
CMD ["memo", "--db", "/data/memo.db", "--http", "--host", "0.0.0.0", "--port", "8000"]
