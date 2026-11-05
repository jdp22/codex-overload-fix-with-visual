# 本地实时监控页

这个页面只读取 relay 的日志元数据，不读取请求体、Header 或凭证。默认只绑定到 `127.0.0.1`。

要在“最近响应内容”栏目显示上游返回的文本增量，需要在 relay 启动参数中显式加入 `--content-log`。内容捕获默认关闭，避免把响应文本写入本地文件：

```powershell
python relay\relay.py --port 8787 --log "$HOME\.codex-overload-fix\relay.log" --content-log "$HOME\.codex-overload-fix\content-feed.jsonl"
```

捕获器只记录 `response.output_text.delta/done`，每段最多 4000 个字符，文件约 2 MB 轮转，不记录请求体、Header 或凭证。

如果 relay 已由安装器作为后台服务运行，请把 `--content-log` 加到对应的 launcher/service 命令中，不要再启动第二个占用 8787 端口的 relay。当前安装器不会自动管理这个可选参数。

Linux 服务器可以在安装时直接传入自定义上游和内容 feed：

```bash
bash scripts/install-linux.sh --linger --no-config --require-proxy \
  --proxy http://127.0.0.1:8890 \
  --upstream api.example.com/v1 \
  --content-log "$HOME/.local/state/codex-overload-fix/content-feed.jsonl"
```

`--no-config` 会保留 Codex 配置不变；自定义 provider 需要由管理员按实际 API 的认证方式手动指向 `http://127.0.0.1:8787`。

启动 dashboard（从仓库根目录执行；脚本会自动寻找 Python 3.9+）：

```powershell
powershell -ExecutionPolicy Bypass -File dashboard\start-dashboard.ps1
```

也可以直接运行服务：

```powershell
python dashboard\server.py --log "$HOME\.codex-overload-fix\relay.log" --port 8899
```

打开 <http://127.0.0.1:8899/>。页面每 2 秒轮询 `/api/metrics`，显示请求数、成功率、处理中请求、重试次数和原因、按分钟趋势、最近事件及 relay/proxy TCP 健康状态。

“最近请求”表会记录每条请求的模型、`reasoning effort`、尝试次数、从触发到结束的总耗时，以及文本流的输出速度（字符/秒）。输出速度只在启用 `--content-log` 且收到文本响应时可用；没有文本增量的请求显示为 `—`。
