#!/usr/bin/env bash
set -euo pipefail

SERVICE="codex-overload-dashboard"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="$UNIT_DIR/$SERVICE.service"

systemctl --user disable --now "$SERVICE.service" >/dev/null 2>&1 || true
rm -f "$UNIT_PATH"
systemctl --user daemon-reload
printf 'Dashboard service removed. Logs and response content were kept.\n'
