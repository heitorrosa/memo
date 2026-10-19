#!/usr/bin/env bash
# Mode-aware healthcheck: the entrypoint writes the mode it started into
# /tmp/memo-mode, because the three launchables expose different surfaces.
#   all     -> the public port (nginx) proxying /health
#   server  -> the memo server on its own port
#   ui      -> the dashboard itself (the API it fronts may live elsewhere)
set -u

mode="$(cat /tmp/memo-mode 2>/dev/null || echo all)"

case "$mode" in
  server)
    exec python -m memo.server --health "http://127.0.0.1:${MEMO_PORT:-5000}/health"
    ;;
  ui)
    exec python - <<'PY'
import urllib.request

try:
    with urllib.request.urlopen("http://127.0.0.1:8080/ui/", timeout=5) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
    ;;
  *)
    exec python -m memo.server --health "http://127.0.0.1:8080/health"
    ;;
esac
