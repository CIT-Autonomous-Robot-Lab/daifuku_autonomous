param(
    [string]$Container = 'daifuku-raspicat-dev',
    [string]$PodmanConnection = 'podman-hyperv-root',
    [ValidateRange(0, 999)]
    [int]$XDisplay = 400,
    [string]$RvizConfig = '/workspaces/daifuku_autonomous/src/autonomous_nav/rviz/nav2_default.rviz',
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'

podman system connection default $PodmanConnection *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Podman connection '$PodmanConnection' was not found. Start the podman-hyperv machine first."
}

$running = podman inspect --format '{{.State.Running}}' $Container 2>$null
if ($LASTEXITCODE -ne 0 -or $running.Trim() -ne 'true') {
    throw "Container '$Container' is not running. Run .\docker_dev\tools\windows\up.ps1 first."
}

# Display :400 maps to TCP 6400 and avoids the WinNAT-reserved range around
# TCP 6000 that can prevent the usual X display :0 from starting.
$xPort = 6000 + $XDisplay
$xServer = 'C:\Program Files\VcXsrv\vcxsrv.exe'
$xListener = Get-NetTCPConnection -LocalPort $xPort -State Listen -ErrorAction SilentlyContinue
if (-not $xListener) {
    if (-not (Test-Path -LiteralPath $xServer)) {
        throw "VcXsrv was not found at '$xServer'. Install or start an X server on TCP $xPort."
    }

    Start-Process -FilePath $xServer -ArgumentList @(
        ":$XDisplay", '-multiwindow', '-clipboard', '-ac', '-nowgl', '-listen', 'tcp'
    )
    Start-Sleep -Seconds 3
    $xListener = Get-NetTCPConnection -LocalPort $xPort -State Listen -ErrorAction SilentlyContinue
    if (-not $xListener) {
        throw "VcXsrv did not start listening on TCP $xPort."
    }
}

podman exec $Container test -f $RvizConfig
if ($LASTEXITCODE -ne 0) {
    throw "RViz config does not exist in the container: $RvizConfig"
}

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

$display = "host.docker.internal:$XDisplay.0"
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

