#!/usr/bin/env bash
# memo: one image, three launchables, one public port.
#
#   all    (default) memo server on 127.0.0.1:5000 (internal) + nginx on :8080 (public)
#   server             memo server only, on 0.0.0.0:${MEMO_PORT:-5000}
#   ui                 nginx only on :8080, proxying the API to ${MEMO_API:-http://127.0.0.1:5000}
set -euo pipefail

MEMO_DB="${MEMO_DB:-/data/memo.db}"
MEMO_PORT="${MEMO_PORT:-5000}"
MEMO_API="${MEMO_API:-http://127.0.0.1:5000}"
MEMO_LOG_FILE="${MEMO_LOG_FILE:-/tmp/memo-server.log}"
MEMO_HEALTH_TIMEOUT="${MEMO_HEALTH_TIMEOUT:-30}"
NGINX_TEMPLATE="${MEMO_NGINX_TEMPLATE:-/opt/memo/docker/nginx.conf}"
NGINX_CONF="${MEMO_NGINX_CONF:-/etc/nginx/conf.d/default.conf}"

memo_pid=""
nginx_pid=""

log() { echo "memo: $*" >&2; }

stop_children() {
    [ -n "$memo_pid" ] && kill "$memo_pid" 2>/dev/null || true
    [ -n "$nginx_pid" ] && kill "$nginx_pid" 2>/dev/null || true
}
# SIGTERM (docker stop) must reach the memo server, not leave it to be killed.
trap 'stop_children; exit 143' TERM INT
trap stop_children EXIT

usage() {
    cat >&2 <<EOF
memo: unknown mode "${1}"
modes:
  all     (default) memo server on 127.0.0.1:5000 + nginx on :8080
  server  memo server only on 0.0.0.0:${MEMO_PORT}
  ui      nginx only on :8080, proxying the API to ${MEMO_API}
EOF
    exit 2
}

# nginx needs the upstream before it starts: a name is resolved by nginx
# through the resolver, so resolve it here and hand nginx an address.
render_nginx() {
    python - "$NGINX_TEMPLATE" "$NGINX_CONF" <<'PY'
import ipaddress
import os
import socket
import sys
from urllib.parse import urlsplit

template_path, out_path = sys.argv[1], sys.argv[2]
target = os.environ.get("MEMO_API", "http://127.0.0.1:5000")


def is_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


raw = target if "://" in target else "http://" + target
parts = urlsplit(raw)
host = parts.hostname or ""
if host and not is_ip(host):
    try:
        host = socket.gethostbyname(host)
    except OSError:
        pass
port = f":{parts.port}" if parts.port else ""
upstream = f"{parts.scheme}://{host}{port}{parts.path or ''}"

resolver = "127.0.0.11"
try:
    with open("/etc/resolv.conf", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("nameserver"):
                resolver = line.split()[1]
                break
except OSError:
    pass

with open(template_path, encoding="utf-8") as handle:
    text = handle.read()
text = text.replace("__MEMO_API_TARGET__", upstream)
text = text.replace("__RESOLVER__", resolver)
with open(out_path, "w", encoding="utf-8") as handle:
    handle.write(text)
PY
}

wait_for_health() {
    local url="http://127.0.0.1:5000/health"
    local deadline=$((SECONDS + MEMO_HEALTH_TIMEOUT))
    while [ "$SECONDS" -lt "$deadline" ]; do
        kill -0 "$memo_pid" 2>/dev/null || return 1
        if python -m memo.server --health "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

run_all() {
    [ -n "${MEMO_TOKEN:-}" ] || { log "MEMO_TOKEN is required"; exit 2; }
    log "starting the memo server on 127.0.0.1:5000 (db ${MEMO_DB})"
    memo --db "$MEMO_DB" --http --host 127.0.0.1 --port 5000 \
        > >(tee -a "$MEMO_LOG_FILE" >&2) 2>&1 &
    memo_pid=$!
    if ! wait_for_health; then
        log "the memo server did not become healthy within ${MEMO_HEALTH_TIMEOUT}s"
        [ -f "$MEMO_LOG_FILE" ] && cat "$MEMO_LOG_FILE" >&2
        exit 1
    fi
    render_nginx
    log "starting nginx on :8080"
    nginx -g 'daemon off;' &
    nginx_pid=$!
    # The memo server dying must take the container down, so the restart
    # policy brings the pair back up together.
    wait -n "$memo_pid" "$nginx_pid" || true
    log "a child process exited; stopping the container"
    exit 1
}

run_server() {
    [ -n "${MEMO_TOKEN:-}" ] || { log "MEMO_TOKEN is required"; exit 2; }
    exec memo --db "$MEMO_DB" --http --host 0.0.0.0 --port "$MEMO_PORT"
}

run_ui() {
    render_nginx
    exec nginx -g 'daemon off;'
}

case "${1:-all}" in
    all) echo all > /tmp/memo-mode; run_all ;;
    server) echo server > /tmp/memo-mode; run_server ;;
    ui) echo ui > /tmp/memo-mode; run_ui ;;
    *) usage "${1:-}" ;;
esac
