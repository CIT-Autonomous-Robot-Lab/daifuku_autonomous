param(
    [string]$Container = 'daifuku-raspicat-dev',
    [string]$PodmanConnection = 'podman-hyperv-root',
    [ValidateRange(0, 999)]
    [int]$XDisplay = 400,
    [string]$RvizConfig = '/workspaces/daifuku_autonomous/src/daifuku_stack/rviz/navigation.rviz',
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

Set-PodmanConnection -Name $PodmanConnection

$running = podman inspect --format '{{.State.Running}}' $Container 2>$null
if ($LASTEXITCODE -ne 0 -or $running.Trim() -ne 'true') {
    throw "Container '$Container' is not running. Run .\docker\dev\tools\windows\up.ps1 first."
}

if (-not (Start-XServer $XDisplay)) {
    throw "No X server is listening on TCP $(Get-XPort $XDisplay). Install VcXsrv at 'C:\Program Files\VcXsrv\vcxsrv.exe' or start an X server yourself."
}

$RvizConfig = Resolve-ContainerFile -Container $Container -Path $RvizConfig

$rvizPid = podman exec $Container bash -lc 'pgrep -x rviz2 | head -n 1' 2>$null
if ($rvizPid) {
    if (-not $Restart) {
        Write-Host "RViz is already running in '$Container' (PID $($rvizPid.Trim()))."
        Write-Host "Use -Restart to restart it."
        exit 0
    }

    podman exec $Container bash -lc 'pkill -x rviz2 || true'
    Start-Sleep -Seconds 1
}

$display = Get-DisplayTarget $XDisplay
$launchCommand = @"
source /opt/ros/humble/setup.bash
if [ -f /workspaces/daifuku_autonomous/install/setup.bash ]; then
  source /workspaces/daifuku_autonomous/install/setup.bash
fi
exec rviz2 -d '$RvizConfig' >/tmp/rviz.log 2>&1
"@

podman exec -d -e "DISPLAY=$display" $Container bash -lc $launchCommand
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to start RViz.'
}

Start-Sleep -Seconds 2
$rvizPid = podman exec $Container bash -lc 'pgrep -x rviz2 | head -n 1' 2>$null
if (-not $rvizPid) {
    podman exec $Container bash -lc 'tail -n 50 /tmp/rviz.log 2>/dev/null || true'
    throw 'RViz exited during startup. The container log is shown above.'
}

Write-Host "RViz started (PID $($rvizPid.Trim()), DISPLAY=$display)."
Write-Host "Log: podman exec $Container tail -n 50 /tmp/rviz.log"

