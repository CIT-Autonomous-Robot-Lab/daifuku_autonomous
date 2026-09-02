# Windows のホストスクリプトが共有するヘルパ。dot-source して使う:
#   . (Join-Path $PSScriptRoot 'common.ps1')

# $PSScriptRoot = <repo>/docker/dev/tools/windows
$RaspicatDevDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$RaspicatComposeFile = Join-Path $RaspicatDevDir 'compose.yaml'
# <repo>/docker/dev -> <repo>/docker -> <repo>
$RaspicatRepoDir = Split-Path -Parent (Split-Path -Parent $RaspicatDevDir)
# compose.yaml はリポジトリルートをここへマウントする。
$RaspicatWorkspace = '/workspaces/daifuku_autonomous'

function Resolve-ContainerFile {
    # リポジトリ内のファイルを $Container から読めるパスにして返す。
    #
    # **Hyper-V の podman マシンは Windows のバインドマウントを時々失う** —
    # コンテナは動いたままワークスペース配下だけが "Input/output error" になる
    # (名前付きボリュームは VM 内なので無事)。そのときは落とさずコピーで渡し、
    # GUI ツールが使えるようにする。
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
    # Podman の compose プロバイダとして Docker Compose を使う。Docker Desktop が
    # CLI を desktop-linux コンテキストに残すことがあるので、Podman の Hyper-V の
    # API パイプを明示的に指す。
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
    # Hyper-V / WinNAT が再起動後に TCP 6000 を予約することがある。ディスプレイ
    # :400 = TCP 6400 なら実測した予約範囲の外に出る。
    param([int]$XDisplay)

    6000 + $XDisplay
}

function Start-XServer {
    # X サーバが待ち受けていれば $true。GUI が無いことを警告とするか失敗とするかは
    # 呼び出し元が決める。
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
