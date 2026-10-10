# memo v3 — single container-first server (spec §4). The embedding weights are
# NOT baked into the image; they ride a read-only bind mount of ./models (populated
# by scripts/fetch_model.py, wired in docker-compose.yml), so a rebuild never
# re-downloads them from HuggingFace.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir . fastembed uvicorn

VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -m memo.server --health http://127.0.0.1:8000/health || exit 1
CMD ["memo", "--db", "/data/memo.db", "--http", "--host", "0.0.0.0", "--port", "8000"]
