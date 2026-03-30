param(
    [switch]$BotOnly,
    [switch]$WebOnly,
    [switch]$NoLog
)

if ($BotOnly) {
    $services = @("bot")
} elseif ($WebOnly) {
    $services = @("web")
} else {
    $services = @("bot", "web")
}

function Write-Log  { param($msg) Write-Host "[deploy] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "[deploy] $msg" -ForegroundColor Yellow }

if (-not (Test-Path ".env")) {
    Write-Warn ".env not found, make sure TELEGRAM_BOT_TOKEN is set"
}

Write-Log "Building: $($services -join ', ')"
docker compose build --no-cache @services

Write-Log "Restarting services..."
docker compose up -d --force-recreate @services

Write-Log "Waiting for services to start..."
Start-Sleep -Seconds 3

foreach ($svc in $services) {
    $output = docker compose ps $svc 2>&1 | Out-String
    if ($output -match "Up|running") {
        Write-Log "${svc}: running"
    } else {
        Write-Warn "${svc}: may still be starting, check logs below"
    }
}

if (-not $NoLog) {
    Write-Host ""
    Write-Host "----------------------------------------" -ForegroundColor Cyan
    Write-Host "  Live logs (Ctrl+C to stop)" -ForegroundColor Cyan
    Write-Host "----------------------------------------" -ForegroundColor Cyan
    Write-Host ""
    docker compose logs -f --tail=50 @services
}
