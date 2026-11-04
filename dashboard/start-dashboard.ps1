param(
    [int]$Port = 8899,
    [string]$Log = "$HOME\.codex-overload-fix\relay.log"
)

$ErrorActionPreference = "Stop"
$dashboardRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$server = Join-Path $dashboardRoot "server.py"

if (-not (Test-Path -LiteralPath $server)) {
    throw "Dashboard server not found: $server"
}

Write-Host "Codex Relay dashboard: http://127.0.0.1:$Port/"
Write-Host "Reading: $Log"
python $server --host 127.0.0.1 --port $Port --log $Log

