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

$pythonCommand = $null
$pythonPrefix = @()
foreach ($candidate in @("python", "py", "python3")) {
    $command = Get-Command $candidate -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) { continue }
    $probe = if ($candidate -eq "py") { @("-3", "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)") } else { @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)") }
    try {
        & $command.Source @probe *> $null
        if ($LASTEXITCODE -eq 0) {
            $pythonCommand = $command.Source
            $pythonPrefix = if ($candidate -eq "py") { @("-3") } else { @() }
            break
        }
    } catch { }
}

if (-not $pythonCommand) {
    throw "Python 3.9+ not found. Install Python and re-run this script."
}

& $pythonCommand @pythonPrefix $server --host 127.0.0.1 --port $Port --log $Log
