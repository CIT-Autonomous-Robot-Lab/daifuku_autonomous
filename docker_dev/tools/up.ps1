param(
    [string]$EthernetAlias = '',
    [string]$InternetAlias = '',
    [string]$PodmanConnection = 'podman-hyperv-root',
    [string]$PodmanPipe = 'podman-hyperv',
    [ValidateRange(0, 999)]
    [int]$XDisplay = 400
)

$ErrorActionPreference = 'Stop'
$toolsDir = $PSScriptRoot
$devDir = Split-Path -Parent $toolsDir
$networkScript = Join-Path $toolsDir 'network-windows.ps1'
$arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $networkScript, '-Mode', 'Static')
if ($EthernetAlias) { $arguments += @('-EthernetAlias', $EthernetAlias) }
if ($InternetAlias) { $arguments += @('-InternetAlias', $InternetAlias) }

$process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arguments
if ($process.ExitCode -ne 0) { throw "Windows static network setup failed with exit code $($process.ExitCode)." }

# Docker Compose is used as Podman's compose provider. Docker Desktop may
# leave the CLI on the desktop-linux context, so explicitly direct it to the
# Podman Hyper-V API pipe.
podman system connection default $PodmanConnection
if ($LASTEXITCODE -ne 0) { throw "Podman connection '$PodmanConnection' was not found." }

$pipePath = "\\.\pipe\$PodmanPipe"
if (-not (Test-Path -LiteralPath $pipePath)) {
    throw "Podman Hyper-V API pipe '$pipePath' is unavailable. Start the podman-hyperv machine first."
}

$env:DOCKER_HOST = "npipe:////./pipe/$PodmanPipe"
Remove-Item Env:DOCKER_CONTEXT -ErrorAction SilentlyContinue

# Hyper-V/WinNAT can reserve TCP port 6000 after a reboot. Display :400 uses
# TCP 6400 and remains outside the observed reserved range.
$env:DISPLAY = "host.docker.internal:$XDisplay.0"
$xPort = 6000 + $XDisplay
$xServer = 'C:\Program Files\VcXsrv\vcxsrv.exe'
$xListener = Get-NetTCPConnection -LocalPort $xPort -State Listen -ErrorAction SilentlyContinue
if (-not $xListener -and (Test-Path -LiteralPath $xServer)) {
    Start-Process -FilePath $xServer -ArgumentList @(
        ":$XDisplay", '-multiwindow', '-clipboard', '-ac', '-nowgl', '-listen', 'tcp'
    )
    Start-Sleep -Seconds 3
    $xListener = Get-NetTCPConnection -LocalPort $xPort -State Listen -ErrorAction SilentlyContinue
}
if (-not $xListener) {
    Write-Warning "No X server is listening on TCP $xPort; RViz GUI will not open."
}

docker version *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker CLI could not reach Podman through '$pipePath'." }

docker compose -f (Join-Path $devDir 'compose.yaml') up -d --build
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed.' }
docker compose -f (Join-Path $devDir 'compose.yaml') ps
