#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${PORT:-5000}"
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
    printf 'Invalid PORT: %s (expected 1-65535)\n' "$PORT" >&2
    exit 2
fi

if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    PYTHON="$(command -v python)"
else
    printf 'Python was not found. Install Python 3 and the requirements in requirements.txt.\n' >&2
    exit 127
fi

if ! command -v cloudflared >/dev/null 2>&1; then
    printf 'cloudflared was not found in PATH.\n' >&2
    printf 'Arch Linux: sudo pacman -S cloudflared\n' >&2
    printf 'Other systems: install cloudflared from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/\n' >&2
    exit 127
fi

export PORT
export FLASK_DEBUG=0

TUNNEL_LOG="$(mktemp)"
APP_PID=""
TUNNEL_PID=""
TAIL_PID=""

cleanup() {
    set +e
    [[ -n "$TAIL_PID" ]] && kill "$TAIL_PID" 2>/dev/null
    [[ -n "$TUNNEL_PID" ]] && kill "$TUNNEL_PID" 2>/dev/null
    [[ -n "$APP_PID" ]] && kill "$APP_PID" 2>/dev/null
    [[ -n "$TAIL_PID" ]] && wait "$TAIL_PID" 2>/dev/null
    [[ -n "$TUNNEL_PID" ]] && wait "$TUNNEL_PID" 2>/dev/null
    [[ -n "$APP_PID" ]] && wait "$APP_PID" 2>/dev/null
    rm -f "$TUNNEL_LOG"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"$PYTHON" app.py &
APP_PID=$!
cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:${PORT}" >"$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

TUNNEL_URL=""
for ((attempt = 0; attempt < 60; attempt++)); do
    if ! kill -0 "$APP_PID" 2>/dev/null; then
        printf 'Flask exited before the public tunnel was ready.\n' >&2
        exit 1
    fi
    if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
        cat "$TUNNEL_LOG" >&2
        printf 'cloudflared exited before creating a public URL.\n' >&2
        exit 1
    fi

    TUNNEL_URL="$(grep -Eo 'https://[[:alnum:]-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -n 1 || true)"
    [[ -n "$TUNNEL_URL" ]] && break
    sleep 1
done

if [[ -z "$TUNNEL_URL" ]]; then
    cat "$TUNNEL_LOG" >&2
    printf 'Timed out waiting for cloudflared to provide a public URL.\n' >&2
    exit 1
fi

tail -n 0 -f "$TUNNEL_LOG" &
TAIL_PID=$!

printf '\nPublic URL: %s\n' "$TUNNEL_URL"
printf 'Finance page: %s/finance\n' "$TUNNEL_URL"
printf 'Press Ctrl+C to stop Flask and cloudflared.\n\n'

wait -n "$APP_PID" "$TUNNEL_PID" "$TAIL_PID"