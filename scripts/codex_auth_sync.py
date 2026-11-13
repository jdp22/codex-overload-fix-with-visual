#!/usr/bin/env python3
"""Reload a managed Codex daemon after a ChatGPT login identity changes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time


def identity(auth_path: Path) -> tuple[str, str] | None:
    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("auth_mode") != "chatgpt":
            return None
        tokens = data.get("tokens")
        if not isinstance(tokens, dict):
            return None
        account = tokens.get("account_id") or ""
        owner = tokens.get("login_owner_id") or ""
        if not all(isinstance(v, str) and not any(c in v for c in "\t\r\n")
                   for v in (account, owner)):
            return None
        return (account, owner) if account or owner else None
    except (OSError, ValueError):
        # A login may briefly leave the file missing or partially written.
        return None


def load_state(path: Path) -> tuple[str, str] | None:
    try:
        parts = path.read_text(encoding="utf-8").rstrip("\r\n").split("\t")
        if len(parts) == 2 and any(parts):
            return parts[0], parts[1]
    except (OSError, ValueError):
        pass
    return None


def save_state(path: Path, value: tuple[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\t".join(value) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def sync_once(auth_path: Path, state_path: Path, command: list[str],
              settle_seconds: float = 2, restart_timeout: float = 120) -> str:
    current = identity(auth_path)
    if current is None:
        return "unavailable"
    previous = load_state(state_path)
    if previous is None:
        save_state(state_path, current)
        return "initialized"
    if previous == current:
        return "unchanged"
    time.sleep(settle_seconds)
    if identity(auth_path) != current:
        return "changed_again"
    print("Codex login identity changed; restarting app-server.", flush=True)
    try:
        result = subprocess.run(command, timeout=restart_timeout,
                                capture_output=True, text=True, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        # Do not log daemon output, auth contents, or potentially secret argv.
        print(f"App-server restart failed ({type(exc).__name__}); will retry.",
              flush=True)
        return "retry"
    if result.returncode != 0:
        print(f"App-server restart exited {result.returncode}; will retry.",
              flush=True)
        return "retry"
    # A failed restart must leave the old identity pending for the next poll.
    save_state(state_path, current)
    print("App-server restart succeeded; login identity synchronized.", flush=True)
    return "restarted"


def positive_number(value: str) -> float:
    number = float(value)
    if not 0 < number < float("inf"):
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def main() -> None:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth-file", type=Path, default=codex_home / "auth.json")
    parser.add_argument("--state-file", type=Path,
                        default=state_home / "codex-overload-fix/codex-auth-identity")
    parser.add_argument("--codex", default="codex", help="Codex executable path")
    parser.add_argument("--poll-seconds", type=positive_number, default=5)
    parser.add_argument("--retry-seconds", type=positive_number, default=30)
    parser.add_argument("--restart-timeout", type=positive_number, default=120)
    args = parser.parse_args()
    command = [args.codex, "app-server", "daemon", "restart"]
    while True:
        result = sync_once(args.auth_file, args.state_file, command,
                           restart_timeout=args.restart_timeout)
        time.sleep(args.retry_seconds if result == "retry" else args.poll_seconds)


if __name__ == "__main__":
    main()
