# Workaround for Docker Desktop 4.60.1 on this Windows machine.
# Preserves stale socket directories instead of deleting data.
$ErrorActionPreference = 'Stop'
if (Get-Process -Name 'com.docker.backend' -ErrorAction SilentlyContinue) {
    throw 'Quit Docker Desktop first. This repair does not stop a running engine.'
}
$localRoot = [IO.Path]::GetFullPath($env:LOCALAPPDATA)
$socketDirs = @(@{ Relative='Docker\run'; Socket='dockerInference' }, @{ Relative='docker-secrets-engine'; Socket='engine.sock' })
foreach ($item in $socketDirs) {
    $socketDir = [IO.Path]::GetFullPath((Join-Path $localRoot $item.Relative))
    if (-not $socketDir.StartsWith($localRoot + '\')) { throw 'Unexpected runtime path' }
    if (-not (Test-Path -LiteralPath $socketDir)) { continue }
    $entries = @(Get-ChildItem -LiteralPath $socketDir -Force)
    if ($entries.Count -eq 1 -and $entries[0].Name -eq $item.Socket -and ($entries[0].Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        $backupDir = $socketDir + '-recovery-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
        if (-not $backupDir.StartsWith($localRoot + '\')) { throw 'Unexpected backup path' }
        Move-Item -LiteralPath $socketDir -Destination $backupDir
        New-Item -ItemType Directory -Path $socketDir | Out-Null
        Write-Host "Preserved stale runtime in: $backupDir"
    } elseif ($entries.Count -gt 0) {
        throw "Unexpected files in $socketDir. Nothing in this directory was changed."
    }
}
docker desktop start --detach
