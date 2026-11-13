# Remote Codex login recovery

Keep `model_provider = "openai"` and the existing `openai_base_url` relay setting.
Changing account does not require renaming the provider or rewriting session files.
The [official authentication documentation](https://learn.chatgpt.com/docs/auth)
describes ChatGPT login and how to check it with `codex login status`.

## Diagnose before restarting

- `usage_limit_reached` / `usageLimitExceeded` means the account has exhausted an
  allowance. Retrying cannot restore that allowance. Check the reset time returned
  by the service. The relay passes this error through without retrying it.
- A new login can update `auth.json` while a long-lived app-server retains the old
  login. Restart that daemon after login; existing sessions remain on disk.
- Repeated WebSocket 405 messages followed by a successful HTTP response are a
  transport fallback, not evidence that authentication failed.
- A service being `active` does not prove requests work. Verify a real Codex turn
  and a harmless tool call, then check the original session.

## Optional login watcher (Linux, Python 3.9+)

This watches the **file-backed ChatGPT login** in `~/.codex/auth.json`. It restarts
the managed app-server when the account ID or login-owner ID changes. Ordinary
token refreshes do not trigger restarts. It does not support keyring-only login,
restart standalone `--no-daemon` clients, resume paused goals, or solve quota limits.
Older clients without a login-owner ID need a manual restart after logging in to
the same account again. A daemon restart may interrupt active turns.

Run from this repository, as the same user that runs Codex:

```bash
install -Dm700 scripts/codex_auth_sync.py ~/.local/bin/codex-auth-sync.py
install -Dm600 scripts/codex-auth-sync.service ~/.config/systemd/user/codex-auth-sync.service
```

The sample unit expects `/usr/bin/python3` and `~/.local/bin/codex`; adjust its
`ExecStart` if your installation uses other paths. For custom homes, supply
`--auth-file` and `--state-file` to the script. The helper's `--help` lists options.

### Preserve the proxy across restarts

**User services do not inherit `.bashrc`.** When Codex needs an HTTP proxy, create
`~/.config/systemd/user/codex-auth-sync.service.d/proxy.conf` with your actual proxy
address (the following port is only an example):

```ini
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:7890"
Environment="HTTPS_PROXY=http://127.0.0.1:7890"
Environment="http_proxy=http://127.0.0.1:7890"
Environment="https_proxy=http://127.0.0.1:7890"
Environment="NO_PROXY=localhost,127.0.0.1,::1"
Environment="no_proxy=localhost,127.0.0.1,::1"
```

This also passes the proxy to daemons started by this watcher. The running daemon
keeps its existing environment until restarted. Independently started Codex
processes need the same proxy environment in their launching shell. Keep the
relay's explicit `--proxy` and `--require-proxy` settings; environment variables
alone are not a machine-wide network kill switch.

```bash
systemctl --user daemon-reload
systemctl --user enable --now codex-auth-sync.service
# If updating a watcher that is already running:
systemctl --user restart codex-auth-sync.service
journalctl --user -u codex-auth-sync.service -n 20 --no-pager
```

On first installation the watcher records a baseline without restarting Codex.
If recovering a stale daemon now, run `codex app-server daemon restart` from a
shell with the correct proxy environment. Check lingering with
`loginctl show-user "$USER" -p Linger`; enabling it may require administrator help
on your server so user services survive logout.

On a failed or timed-out restart the watcher retains the previous identity and
retries after 30 seconds. It records only account/login-owner identifiers, with
mode 0600; tokens and daemon command output are never logged.

To remove just the watcher:

```bash
systemctl --user disable --now codex-auth-sync.service
```

The relay, login and session files are unchanged by disabling this helper.
