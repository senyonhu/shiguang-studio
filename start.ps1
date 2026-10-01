param([switch]$SkipJudge)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtimePath = Join-Path $PSScriptRoot '.runtime'
New-Item -ItemType Directory -Force -Path $runtimePath | Out-Null
if (-not (Test-Path 'node_modules')) { npm.cmd ci; if ($LASTEXITCODE -ne 0) { throw 'npm install failed' } }
if (-not (Test-Path '.venv\Scripts\python.exe')) { python -m venv .venv; & '.\.venv\Scripts\python.exe' -m pip install -r backend/requirements.txt }
if (-not $SkipJudge) {
    docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Start-Process -FilePath 'C:\Program Files\Docker\Docker\Docker Desktop.exe' -WindowStyle Hidden; Write-Host 'Please wait for Docker Desktop to start, then run this launcher again.'; exit 1 }
    docker compose -f infra/compose.yaml up -d
    if ($LASTEXITCODE -ne 0) { throw 'Judge0 startup failed. Check Docker connectivity.' }
}
npm.cmd run story
$apiReady=$false
try { $null=Invoke-RestMethod 'http://127.0.0.1:8000/openapi.json' -TimeoutSec 2; $apiReady=$true } catch {}
if (-not $apiReady) {
    $apiProcess=Start-Process -FilePath "$PSScriptRoot\.venv\Scripts\python.exe" -ArgumentList '-m uvicorn backend.app:app --host 127.0.0.1 --port 8000' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput "$runtimePath\api.log" -RedirectStandardError "$runtimePath\api-error.log" -PassThru
    $apiProcess.Id | Set-Content "$runtimePath\api.pid"
}
$webReady=$false
try { $null=Invoke-WebRequest 'http://127.0.0.1:5173' -TimeoutSec 2; $webReady=$true } catch {}
if (-not $webReady) {
    $webProcess=Start-Process -FilePath 'node.exe' -ArgumentList 'node_modules/vite/bin/vite.js --host 127.0.0.1' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput "$runtimePath\web.log" -RedirectStandardError "$runtimePath\web-error.log" -PassThru
    $webProcess.Id | Set-Content "$runtimePath\web.pid"
}
Write-Host 'Demo: http://127.0.0.1:5173'
Write-Host 'Health: http://127.0.0.1:8000/api/health'
Write-Host 'Logs: .runtime'
