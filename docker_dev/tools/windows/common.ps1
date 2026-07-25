# Shared helpers for the Windows host scripts. Dot-source it with:
#   . (Join-Path $PSScriptRoot 'common.ps1')

$RaspicatDevDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$RaspicatComposeFile = Join-Path $RaspicatDevDir 'compose.yaml'

function Set-PodmanConnection {
    param([string]$Name = 'podman-hyperv-root')

    podman system connection default $Name *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Podman connection '$Name' was not found. Start the podman-hyperv machine first."
    }
}

function Set-DockerToPodman {
    # Docker Compose is used as Podman's compose provider. Docker Desktop may
    # leave the CLI on the desktop-linux context, so point it at the Podman
    # Hyper-V API pipe explicitly.
    param(
        [string]$Connection = 'podman-hyperv-root',
        [string]$Pipe = 'podman-hyperv'
    )

    Set-PodmanConnection -Name $Connection

    $pipePath = "\\.\pipe\$Pipe"
    if (-not (Test-Path -LiteralPath $pipePath)) {
        throw "Podman Hyper-V API pipe '$pipePath' is unavailable. Start the podman-hyperv machine first."
    }

    $env:DOCKER_HOST = "npipe:////./pipe/$Pipe"
    Remove-Item Env:DOCKER_CONTEXT -ErrorAction SilentlyContinue

    docker version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker CLI could not reach Podman through '$pipePath'."
    }
}

function Get-DisplayTarget {
    param([int]$XDisplay)

    "host.docker.internal:$XDisplay.0"
}

function Get-XPort {
    # Hyper-V/WinNAT can reserve TCP 6000 after a reboot. Display :400 maps to
    # TCP 6400 and stays outside the observed reserved range.
    param([int]$XDisplay)

    6000 + $XDisplay
}

function Start-XServer {
    # Returns $true when an X server is listening for the display. The caller
    # decides whether a missing GUI is a warning or an error.
    param([int]$XDisplay)

    $port = Get-XPort $XDisplay
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        return $true
    }

    $xServer = 'C:\Program Files\VcXsrv\vcxsrv.exe'
    if (-not (Test-Path -LiteralPath $xServer)) {
        return $false
    }

    Start-Process -FilePath $xServer -ArgumentList @(
        ":$XDisplay", '-multiwindow', '-clipboard', '-ac', '-nowgl', '-listen', 'tcp'
    )
    Start-Sleep -Seconds 3
    [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}
