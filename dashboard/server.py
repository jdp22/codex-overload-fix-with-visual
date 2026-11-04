#!/usr/bin/env python3
"""Small stdlib-only dashboard for the local overload-fix relay.

The dashboard intentionally reads only relay.log.  It never reads request
bodies, headers, or credentials.  Start it with::

    python dashboard/server.py --log C:\\Users\\me\\.codex-overload-fix\\relay.log

Then open http://127.0.0.1:8899/.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import threading
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
INDEX_FILE = ROOT / "index.html"

TIMESTAMP = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (?P<msg>.*)$")
REQUEST = re.compile(r"\[(?P<port>\d+)\] (?P<method>\S+) (?P<path>\S+) \((?P<body>\d+)B\)")
MODEL_FIELD = re.compile(r"\bmodel=(?P<model>[^ ]+)")
ATTEMPT_OK = re.compile(r"\[(?P<port>\d+)\] attempt (?P<attempt>\d+): ok, streaming")
ATTEMPT_PASS = re.compile(r"\[(?P<port>\d+)\] attempt (?P<attempt>\d+): upstream (?P<status>\d+), passthrough")
RETRY = re.compile(r"\[(?P<port>\d+)\] attempt (?P<attempt>\d+): retryable \((?P<reason>.*)\)")
GIVE_UP = re.compile(r"\[(?P<port>\d+)\] giving up after (?P<attempt>\d+) attempts")
ABANDON = re.compile(r"\[(?P<port>\d+)\] abandoning retries after (?P<attempt>\d+) attempts")
SSE_EVENT = re.compile(
    r"\s+evt#(?P<number>\d+) (?P<kind>\S+)(?: \[served=(?P<served>[^\]]+)\])?"
)


def parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def probe(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def format_age(seconds: float | None) -> str:
    if seconds is None:
        return "无日志"
    if seconds < 0:
        return "刚刚"
    if seconds < 60:
        return f"{int(seconds)} 秒前"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    return f"{int(seconds // 3600)} 小时前"


def load_lines(log_path: Path) -> list[str]:
    try:
        # relay.py rotates at 8 MiB. Reading the current file in full keeps the
        # parser simple and is small enough for a local dashboard poll.
        return log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (FileNotFoundError, OSError):
        return []


def parse_content_time(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except (TypeError, ValueError):
        return None


def load_content(content_path: Path, cutoff: datetime) -> dict[str, Any]:
    """Read the bounded JSONL response feed without exposing request data."""
    try:
        lines = content_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (FileNotFoundError, OSError):
        return {"available": False, "log_path": str(content_path), "streams": []}

    streams: dict[str, dict[str, Any]] = {}
    for raw in lines[-4000:]:
        try:
            row = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        dt = parse_content_time(row.get("time", ""))
        if dt is None or dt < cutoff:
            continue
        port = str(row.get("port") or "0")
        event = row.get("event")
        item = streams.setdefault(
            port,
            {
                "port": port,
                "started_at": dt.isoformat(timespec="seconds"),
                "last_at": dt.isoformat(timespec="seconds"),
                "status": "streaming",
                "text": "",
                "truncated": False,
            },
        )
        item["last_at"] = dt.isoformat(timespec="seconds")
        if event == "start":
            item["started_at"] = dt.isoformat(timespec="seconds")
            item["status"] = "streaming"
        elif event == "delta":
            text = row.get("text")
            if isinstance(text, str):
                item["text"] = (item["text"] + text)[-12000:]
            item["truncated"] = bool(item["truncated"] or row.get("truncated"))
            item["status"] = "streaming"
        elif event == "done":
            text = row.get("text")
            if not item["text"] and isinstance(text, str):
                item["text"] = text[-12000:]
            item["truncated"] = bool(item["truncated"] or row.get("truncated"))
            item["status"] = "done"

    # Keep visible text ahead of empty tool/reasoning-only streams so the
    # content panel remains useful while a busy client emits many non-text
    # responses.
    rows = sorted(streams.values(), key=lambda row: (bool(row["text"]), row["last_at"]), reverse=True)
    return {"available": True, "log_path": str(content_path), "streams": rows[:20]}


def metrics(
    log_path: Path,
    relay_host: str,
    relay_port: int,
    proxy_port: int,
    window: int,
    content_path: Path,
) -> dict[str, Any]:
    now = datetime.now()
    cutoff = now - timedelta(seconds=max(60, window))
    lines = load_lines(log_path)
    content = load_content(content_path, cutoff)

    # A connection is identified by the relay's client port. Concurrent turns
    # therefore remain separate even though their log lines are interleaved.
    active: dict[str, dict[str, Any]] = {}
    requests: list[dict[str, Any]] = []
    events: deque[dict[str, Any]] = deque(maxlen=80)
    retry_reasons: Counter[str] = Counter()
    served_models: Counter[str] = Counter()
    trend: dict[int, dict[str, int]] = defaultdict(
        lambda: {"requests": 0, "successes": 0, "retries": 0, "failures": 0}
    )
    retries = 0
    successes = 0
    failures = 0
    abandoned = 0
    latest_dt: datetime | None = None

    def bucket(dt: datetime) -> dict[str, int]:
        key = int(dt.timestamp() // 60) * 60
        return trend[key]

    def add_event(dt: datetime, kind: str, label: str, detail: str, port: str | None = None) -> None:
        events.append(
            {
                "time": dt.isoformat(timespec="seconds"),
                "kind": kind,
                "label": label,
                "detail": detail,
                "port": port or "",
            }
        )

    for raw in lines:
        match = TIMESTAMP.match(raw)
        if not match:
            continue
        dt = parse_timestamp(match.group("ts"))
        if dt is None or dt < cutoff:
            continue
        latest_dt = max(latest_dt, dt) if latest_dt else dt
        msg = match.group("msg")

        m = REQUEST.search(msg)
        if m and m.group("method") == "POST" and m.group("path").split("?", 1)[0].endswith("/responses"):
            port = m.group("port")
            body = int(m.group("body"))
            model_match = MODEL_FIELD.search(msg)
            req = {
                "port": port,
                "body_bytes": body,
                "model": model_match.group("model") if model_match else "",
                "started_at": dt.isoformat(timespec="seconds"),
                "attempts": 0,
                "retries": 0,
                "status": "pending",
            }
            requests.append(req)
            active[port] = req
            b = bucket(dt)
            b["requests"] += 1
            add_event(dt, "request", "收到请求", f"/responses · {body / 1024:.1f} KB", port)
            continue

        m = SSE_EVENT.search(msg)
        if m:
            served = m.group("served") or ""
            if served:
                served_models[served] += 1
            # SSE deltas are intentionally omitted from the activity feed.
            # They are very frequent and would hide the request/retry/result
            # events that matter on a monitoring page.
            continue

        m = RETRY.search(msg)
        if m:
            port = m.group("port")
            attempt = int(m.group("attempt"))
            reason = m.group("reason")
            retries += 1
            req = active.get(port)
            if req:
                req["attempts"] = max(req["attempts"], attempt)
                req["retries"] += 1
            reason_lower = reason.lower()
            if "overload" in reason_lower or "capacity" in reason_lower:
                reason_key = "overload"
            elif "connect" in reason_lower or "ssl" in reason_lower or "stream" in reason_lower:
                reason_key = "connection"
            elif "http" in reason_lower:
                reason_key = "http"
            else:
                reason_key = "other"
            retry_reasons[reason_key] += 1
            bucket(dt)["retries"] += 1
            add_event(dt, "retry", "自动重试", reason, port)
            continue

        m = ATTEMPT_OK.search(msg)
        if m:
            port = m.group("port")
            attempt = int(m.group("attempt"))
            req = active.pop(port, None)
            if req:
                req["attempts"] = max(req["attempts"], attempt)
                req["status"] = "success"
                req["completed_at"] = dt.isoformat(timespec="seconds")
            successes += 1
            bucket(dt)["successes"] += 1
            add_event(dt, "success", "成功转发", f"第 {attempt} 次尝试", port)
            continue

        m = ATTEMPT_PASS.search(msg)
        if m:
            port = m.group("port")
            attempt = int(m.group("attempt"))
            status = int(m.group("status"))
            req = active.pop(port, None)
            if req:
                req["attempts"] = max(req["attempts"], attempt)
                req["status"] = "success" if 200 <= status < 400 else "failed"
                req["completed_at"] = dt.isoformat(timespec="seconds")
            if 200 <= status < 400:
                successes += 1
                bucket(dt)["successes"] += 1
                add_event(dt, "success", "上游返回", f"HTTP {status} · 第 {attempt} 次尝试", port)
            else:
                failures += 1
                bucket(dt)["failures"] += 1
                add_event(dt, "failure", "上游错误", f"HTTP {status} · 第 {attempt} 次尝试", port)
            continue

        m = GIVE_UP.search(msg) or ABANDON.search(msg)
        if m:
            port = m.group("port")
            attempt = int(m.group("attempt"))
            req = active.pop(port, None)
            if req:
                req["attempts"] = max(req["attempts"], attempt)
                req["status"] = "failed" if "giving up" in msg else "abandoned"
                req["completed_at"] = dt.isoformat(timespec="seconds")
            failures += 1
            if "abandoning" in msg:
                abandoned += 1
            bucket(dt)["failures"] += 1
            add_event(
                dt,
                "failure",
                "请求失败" if "giving up" in msg else "客户端取消",
                f"{attempt} 次尝试",
                port,
            )

    total = len(requests)
    pending = sum(1 for item in requests if item["status"] == "pending")
    terminal = successes + failures
    success_rate = successes / terminal * 100 if terminal else None
    retry_rate = retries / total * 100 if total else None
    if latest_dt:
        age = max(0.0, (now - latest_dt).total_seconds())
    else:
        age = None

    # Make the trend useful even when the relay has been quiet: fixed buckets
    # let the browser draw a stable time axis instead of jumping around.
    bucket_count = max(1, min(60, max(1, window) // 60))
    end_key = int(now.timestamp() // 60) * 60
    trend_rows = []
    for offset in range(bucket_count - 1, -1, -1):
        key = end_key - offset * 60
        row = trend.get(key, {"requests": 0, "successes": 0, "retries": 0, "failures": 0})
        trend_rows.append({"time": datetime.fromtimestamp(key).isoformat(timespec="minutes"), **row})

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "window_seconds": window,
        "window_label": f"最近 {max(1, window // 60)} 分钟",
        "log_path": str(log_path),
        "content": content,
        "log_exists": log_path.exists(),
        "last_log_at": latest_dt.isoformat(timespec="seconds") if latest_dt else None,
        "last_log_age_seconds": age,
        "last_log_age_label": format_age(age),
        "health": {
            "relay": probe(relay_host, relay_port),
            "proxy": probe(relay_host, proxy_port),
            "relay_address": f"{relay_host}:{relay_port}",
            "proxy_address": f"{relay_host}:{proxy_port}",
            "status": "healthy" if probe(relay_host, relay_port) else "degraded",
        },
        "summary": {
            "requests": total,
            "successful": successes,
            "failed": failures,
            "pending": pending,
            "attempts": sum(int(item["attempts"]) for item in requests),
            "retries": retries,
            "retries_per_request": retries / total if total else None,
            "abandoned": abandoned,
            "success_rate": success_rate,
            "retry_rate": retry_rate,
        },
        "retry_reasons": dict(retry_reasons),
        "served_models": dict(served_models),
        "trend": trend_rows,
        "requests_detail": list(reversed(requests[-30:])),
        "events": list(reversed(events)),
    }


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "CodexRelayDashboard/1.0"

    def log_message(self, _fmt: str, *_args: Any) -> None:
        pass

    @property
    def settings(self) -> argparse.Namespace:
        return self.server.settings  # type: ignore[attr-defined]

    def send_bytes(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/metrics":
            payload = metrics(
                Path(self.settings.log),
                self.settings.relay_host,
                self.settings.relay_port,
                self.settings.proxy_port,
                self.settings.window,
                Path(self.settings.content_log),
            )
            self.send_bytes(200, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False).encode())
            return
        if path in {"/", "/index.html"}:
            try:
                data = INDEX_FILE.read_bytes()
            except OSError:
                self.send_bytes(500, "text/plain; charset=utf-8", b"dashboard index missing\n")
                return
            self.send_bytes(200, "text/html; charset=utf-8", data)
            return
        self.send_bytes(404, "text/plain; charset=utf-8", b"not found\n")


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def default_log() -> str:
    state_dir = os.environ.get("CODEX_OVERLOAD_FIX_STATE")
    if state_dir:
        return str(Path(state_dir) / "relay.log")
    return str(Path.home() / ".codex-overload-fix" / "relay.log")


def default_content_log(log_path: str) -> str:
    return str(Path(log_path).with_name("content-feed.jsonl"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Local dashboard for codex-overload-fix relay.log")
    parser.add_argument("--host", default="127.0.0.1", help="dashboard bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8899, help="dashboard port (default: 8899)")
    parser.add_argument("--log", default=default_log(), help="path to relay.log")
    parser.add_argument("--relay-host", default="127.0.0.1")
    parser.add_argument("--relay-port", type=int, default=8787)
    parser.add_argument("--proxy-port", type=int, default=17890)
    parser.add_argument("--window", type=int, default=3600, help="metrics window in seconds (default: 3600)")
    parser.add_argument("--content-log", default="", help="JSONL response content feed")
    args = parser.parse_args()
    if not args.content_log:
        args.content_log = default_content_log(args.log)
    server = DashboardServer((args.host, args.port), DashboardHandler)
    server.settings = args
    print(f"Codex relay dashboard: http://{args.host}:{args.port}/", flush=True)
    print(f"Log: {args.log}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

