$ErrorActionPreference = 'Stop'
$package = Join-Path $PSScriptRoot 'release\TowerLastLight-1.1.0-win\TowerLastLight.exe'
$sdk = Join-Path $PSScriptRoot 'tools\renpy-8.5.3-sdk\renpy.exe'
if (Test-Path -LiteralPath $package) {
    Start-Process -FilePath $package -WorkingDirectory (Split-Path $package)
} elseif (Test-Path -LiteralPath $sdk) {
    $client = Join-Path $PSScriptRoot 'client'
    Start-Process -FilePath $sdk -ArgumentList ('"'+$client+'"') -WorkingDirectory $PSScriptRoot
} else {
    throw 'Extract release/TowerLastLight-1.1.0-win.zip and run TowerLastLight.exe.'
}
