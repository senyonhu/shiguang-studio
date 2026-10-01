param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$studioRoot = $PSScriptRoot
Set-Location -LiteralPath $studioRoot
$studioUrl = 'http://127.0.0.1:5190/studio.html'
$studioNode = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
if (-not $studioNode) { throw 'Node.js is missing. Please install Node.js 22 or newer.' }
if (-not (Test-Path -LiteralPath (Join-Path $studioRoot 'node_modules/vite/bin/vite.js'))) { throw 'Project dependencies are missing. Run npm ci in this project first.' }
if (-not (Test-Path -LiteralPath (Join-Path $studioRoot 'dist/studio.html'))) {
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Studio build failed. See the message above.' }
}
New-Item -ItemType Directory -Force -Path (Join-Path $studioRoot '.runtime') | Out-Null
function Test-StudioReady {
    try {
        $studioResponse = Invoke-WebRequest -Uri $studioUrl -UseBasicParsing -TimeoutSec 2
        return $studioResponse.StatusCode -eq 200 -and $studioResponse.Content.Contains('studio-')
    } catch { return $false }
}
if (-not (Test-StudioReady)) {
    $studioVite = Join-Path $studioRoot 'node_modules/vite/bin/vite.js'
    $studioProcess = Start-Process -FilePath $studioNode -ArgumentList @(('"' + $studioVite + '"'), 'preview', '--host', '127.0.0.1', '--port', '5190', '--strictPort') -WorkingDirectory $studioRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $studioRoot '.runtime/studio.log') -RedirectStandardError (Join-Path $studioRoot '.runtime/studio-error.log')
    $studioProcess.Id | Set-Content -LiteralPath (Join-Path $studioRoot '.runtime/studio.pid')
    $studioReady = $false
    for ($studioAttempt = 0; $studioAttempt -lt 30; $studioAttempt++) {
        if (Test-StudioReady) { $studioReady = $true; break }
        if ($studioProcess.HasExited) { throw 'Studio did not start. Check .runtime/studio-error.log; port 5190 may be occupied.' }
        Start-Sleep -Milliseconds 300
    }
    if (-not $studioReady) { throw 'Studio startup timed out. Check .runtime/studio-error.log.' }
}
Write-Output ('Studio is ready: ' + $studioUrl)
if (-not $NoBrowser) { Start-Process $studioUrl }
