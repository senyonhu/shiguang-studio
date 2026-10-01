$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$sdkRoot = Join-Path $projectRoot 'tools\renpy-8.5.3-sdk'
$sdkPython = Join-Path $sdkRoot 'lib\py3-windows-x86_64\python.exe'
$entry = Join-Path $sdkRoot 'renpy.py'
$clientRoot = Join-Path $projectRoot 'client'
if (!(Test-Path -LiteralPath $sdkPython)) { throw 'Missing locked RenPy SDK 8.5.3.' }
& $sdkPython $entry $clientRoot lint
if ($LASTEXITCODE) { throw 'RenPy lint failed.' }
& $sdkPython $entry (Join-Path $sdkRoot 'launcher') distribute $clientRoot --package win --no-update --dest (Join-Path $projectRoot 'release')
if ($LASTEXITCODE) { throw 'RenPy packaging failed.' }
