#!/usr/bin/env bash
set -euo pipefail

PORT=8899
PYTHON_PATH=""
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
LOG_PATH="$STATE_HOME/codex-overload-fix/relay.log"
CONTENT_LOG="$STATE_HOME/codex-overload-fix/content-feed.jsonl"
NO_AUTOSTART=0
LINGER=0
SERVICE="codex-overload-dashboard"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="$UNIT_DIR/$SERVICE.service"

say()  { printf '  %s\n' "$*"; }
ok()   { printf '  [ok] %s\n' "$*"; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
usage: install-dashboard-linux.sh [options]

  --port PORT          Dashboard port (default 8899)
  --log PATH           Relay log to read
  --content-log PATH   Optional response content JSONL feed
  --python-path PATH   Python 3.9+ interpreter
  --linger             Enable lingering so the dashboard survives logout
  --no-autostart       Write the unit but do not enable or start it
  -h, --help           Show this message
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --port)         [ $# -ge 2 ] || die "--port needs a value"; PORT="$2"; shift 2 ;;
        --log)          [ $# -ge 2 ] || die "--log needs a value"; LOG_PATH="$2"; shift 2 ;;
        --content-log)  [ $# -ge 2 ] || die "--content-log needs a value"; CONTENT_LOG="$2"; shift 2 ;;
        --python-path)  [ $# -ge 2 ] || die "--python-path needs a value"; PYTHON_PATH="$2"; shift 2 ;;
        --linger)       LINGER=1; shift ;;
        --no-autostart) NO_AUTOSTART=1; shift ;;
        -h|--help)      usage; exit 0 ;;
        *)              die "unknown option: $1 (try --help)" ;;
    esac
done

case "$PORT" in ''|*[!0-9]*) die "port must be a number: $PORT" ;; esac
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "port out of range: $PORT"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
SERVER="$ROOT/dashboard/server.py"
[ -f "$SERVER" ] || die "dashboard/server.py not found under $ROOT"

if [ -z "$PYTHON_PATH" ]; then PYTHON_PATH="$(command -v python3 || true)"; fi
[ -n "$PYTHON_PATH" ] || die "python3 not found"
[ -x "$PYTHON_PATH" ] || die "not executable: $PYTHON_PATH"
"$PYTHON_PATH" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' || die "Python 3.9+ required"

mkdir -p "$UNIT_DIR" "$(dirname "$LOG_PATH")" "$(dirname "$CONTENT_LOG")"
cat > "$UNIT_PATH" <<EOF
[Unit]
Description=Codex relay local dashboard
Documentation=https://github.com/NewBeeM4n/codex-overload-fix

[Service]
Type=simple
ExecStart="$PYTHON_PATH" "$SERVER" --host 127.0.0.1 --port "$PORT" --log "$LOG_PATH" --content-log "$CONTENT_LOG"
WorkingDirectory="$ROOT"
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
ok "unit $UNIT_PATH"

if [ "$NO_AUTOSTART" -eq 1 ]; then
    say "not started (--no-autostart)"
    exit 0
fi

if [ "$LINGER" -eq 1 ]; then loginctl enable-linger "$USER" >/dev/null; fi
systemctl --user daemon-reload
systemctl --user enable "$SERVICE.service" >/dev/null
systemctl --user restart "$SERVICE.service"
ok "systemctl --user enable + restart"

for _ in $(seq 1 30); do
    if "$PYTHON_PATH" - "$PORT" <<'PY' >/dev/null 2>&1
import socket, sys
try:
    with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=0.5):
        pass
except OSError:
    raise SystemExit(1)
PY
    then
        ok "verified dashboard on 127.0.0.1:$PORT"
        exit 0
    fi
    sleep 1
done

die "dashboard did not start; inspect: systemctl --user status $SERVICE"
