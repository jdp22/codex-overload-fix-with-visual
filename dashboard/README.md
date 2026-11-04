# 本地实时监控页

这个页面只读取 relay 的日志元数据，不读取请求体、Header 或凭证。默认只绑定到 `127.0.0.1`。

要在“最近响应内容”栏目显示上游返回的文本增量，需要在 relay 启动参数中显式加入 `--content-log`。内容捕获默认关闭，避免把响应文本写入本地文件：

```powershell
python relay/relay.py ... --content-log "$HOME\.codex-overload-fix\content-feed.jsonl"
```

捕获器只记录 `response.output_text.delta/done`，每段最多 4000 个字符，文件约 2 MB 轮转，不记录请求体、Header 或凭证。

启动：

```powershell
python dashboard/server.py --log "$HOME\.codex-overload-fix\relay.log" --port 8899
```

或：

```powershell
powershell -ExecutionPolicy Bypass -File dashboard/start-dashboard.ps1
```

打开 <http://127.0.0.1:8899/>。页面每 2 秒轮询 `/api/metrics`，显示请求数、成功率、处理中请求、重试次数和原因、按分钟趋势、最近事件及 relay/proxy TCP 健康状态。

