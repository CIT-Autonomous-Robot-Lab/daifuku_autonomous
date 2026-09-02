param(
    [string]$EthernetAlias = '',
    [string]$InternetAlias = '',
    [string]$PodmanConnection = 'podman-hyperv-root',
    [string]$PodmanPipe = 'podman-hyperv',
    [ValidateRange(0, 999)]
    [int]$XDisplay = 400
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

# 固定 LAN の設定には管理者が要るので、このセッションではなく昇格した別プロセスで走らせる。
$arguments = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass',
    '-File', (Join-Path $PSScriptRoot 'network.ps1'),
    '-Mode', 'Static'
)
if ($EthernetAlias) { $arguments += @('-EthernetAlias', $EthernetAlias) }
if ($InternetAlias) { $arguments += @('-InternetAlias', $InternetAlias) }

$process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arguments
if ($process.ExitCode -ne 0) {
    throw "Windows static network setup failed with exit code $($process.ExitCode)."
}

Set-DockerToPodman -Connection $PodmanConnection -Pipe $PodmanPipe

$env:DISPLAY = Get-DisplayTarget $XDisplay
if (-not (Start-XServer $XDisplay)) {
    Write-Warning "No X server is listening on TCP $(Get-XPort $XDisplay); the RViz GUI will not open."
}

docker compose -f $RaspicatComposeFile up -d --build
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed.' }
docker compose -f $RaspicatComposeFile ps
