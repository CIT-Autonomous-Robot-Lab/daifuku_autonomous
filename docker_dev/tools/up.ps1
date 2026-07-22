param(
    [string]$EthernetAlias = '',
    [string]$InternetAlias = ''
)

$ErrorActionPreference = 'Stop'
$toolsDir = $PSScriptRoot
$devDir = Split-Path -Parent $toolsDir
$networkScript = Join-Path $toolsDir 'network-windows.ps1'
$arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $networkScript, '-Mode', 'Enable')
if ($EthernetAlias) { $arguments += @('-EthernetAlias', $EthernetAlias) }
if ($InternetAlias) { $arguments += @('-InternetAlias', $InternetAlias) }

$process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arguments
if ($process.ExitCode -ne 0) { throw "Windows ICS setup failed with exit code $($process.ExitCode)." }

docker compose -f (Join-Path $devDir 'compose.yaml') up -d --build
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed.' }
docker compose -f (Join-Path $devDir 'compose.yaml') ps
