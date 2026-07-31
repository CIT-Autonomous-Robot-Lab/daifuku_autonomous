# Shared helpers for the Windows host scripts. Dot-source it with:
#   . (Join-Path $PSScriptRoot 'common.ps1')

# $PSScriptRoot = <repo>/docker/dev/tools/windows
$RaspicatDevDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$RaspicatComposeFile = Join-Path $RaspicatDevDir 'compose.yaml'
# <repo>/docker/dev -> <repo>/docker -> <repo>
$RaspicatRepoDir = Split-Path -Parent (Split-Path -Parent $RaspicatDevDir)
# compose.yaml mounts the repo root here.
$RaspicatWorkspace = '/workspaces/daifuku_autonomous'

function Resolve-ContainerFile {
    # Returns a path that is readable inside $Container for a file that lives in
    # the repo.
    #
    # The Hyper-V podman machine periodically loses its Windows bind mount: the
    # container keeps running but every access under the workspace fails with
    # "Input/output error" (statfs on /mnt/c). The named volumes (build/install/
    # log) are unaffected because they live inside the VM. When that happens,
    # copy the file in rather than failing, so the GUI tools still work.
    param(
        [Parameter(Mandatory)][string]$Container,
        [Parameter(Mandatory)][string]$Path,
        [string]$StageDir = '/tmp/host-staged'
    )

    podman exec $Container test -r $Path *> $null
    if ($LASTEXITCODE -eq 0) {
        return $Path
    }

    if (-not $Path.StartsWith("$RaspicatWorkspace/")) {
        throw "'$Path' is unreadable in '$Container' and is outside $RaspicatWorkspace, so it cannot be staged from the host."
    }

    $relative = $Path.Substring($RaspicatWorkspace.Length).TrimStart('/')
    $hostPath = Join-Path $RaspicatRepoDir ($relative -replace '/', '\')
    if (-not (Test-Path -LiteralPath $hostPath -PathType Leaf)) {
        throw "'$Path' is unreadable in '$Container' and the host copy '$hostPath' does not exist either."
    }

    $staged = "$StageDir/$relative"
    podman exec $Container mkdir -p (Split-Path -Parent $staged).Replace('\', '/')
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create '$StageDir' in '$Container'."
    }

    podman cp $hostPath "${Container}:$staged"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to copy '$hostPath' into '$Container'."
    }

    Write-Host "Workspace mount is unreadable in '$Container'; staged $relative from the host to $staged."
    return $staged
}

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
