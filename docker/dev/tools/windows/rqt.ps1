param(
    [string]$Container = 'daifuku-raspicat-dev',
    [string]$PodmanConnection = 'podman-hyperv-root',
    [ValidateRange(0, 999)]
    [int]$XDisplay = 400,
    [string]$Plugin = 'daifuku_rqt',
    [switch]$NoStandalone,
    [switch]$ForceDiscover,
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

# rqt runs as a python script, so its process name is python3 and `pgrep -x rqt`
# never matches. Match the interpreter argument instead. The bracket keeps the
# pattern from matching the `bash -lc` that carries it, and the trailing anchor
# keeps it off the siblings: an unanchored /bin/rqt also matches
# /bin/rqt_plot, which docs/usage/control-panel.md tells people to open next to
# this panel. Without the anchor, -Restart would kill it.
$RqtPattern = '[/]bin/rqt( |$)'

Set-PodmanConnection -Name $PodmanConnection

$running = podman inspect --format '{{.State.Running}}' $Container 2>$null
if ($LASTEXITCODE -ne 0 -or $running.Trim() -ne 'true') {
    throw "Container '$Container' is not running. Run .\docker\dev\tools\windows\up.ps1 first."
}

if (-not (Start-XServer $XDisplay)) {
    throw "No X server is listening on TCP $(Get-XPort $XDisplay). Install VcXsrv at 'C:\Program Files\VcXsrv\vcxsrv.exe' or start an X server yourself."
}

# The workspace overlay is what carries daifuku_rqt. Unlike RViz there is no
# single file to stage from the host when the bind mount drops, so say what is
# missing rather than letting rqt come up without the plugin -- with
# -NoStandalone that failure is just an absent menu entry, which explains
# itself even less.
$overlay = "$RaspicatWorkspace/install/setup.bash"
podman exec $Container test -r $overlay *> $null
if ($LASTEXITCODE -ne 0) {
    $reason = "'$overlay' is unreadable in '$Container', so daifuku_rqt is not on the plugin path. Build the workspace with 'build-autonomous', or restart the container if the Windows bind mount was lost."
    if ($NoStandalone) {
        Write-Warning $reason
    } else {
        throw $reason
    }
}

$rqtPid = podman exec $Container bash -lc "pgrep -f '$RqtPattern' | head -n 1" 2>$null
if ($rqtPid) {
    if (-not $Restart) {
        Write-Host "rqt is already running in '$Container' (PID $($rqtPid.Trim()))."
        Write-Host "Use -Restart to restart it."
        exit 0
    }

    podman exec $Container bash -lc "pkill -f '$RqtPattern' || true"
    Start-Sleep -Seconds 1
}

$rqtArgs = @()
if (-not $NoStandalone) {
    $rqtArgs += "--standalone '$Plugin'"
}
if ($ForceDiscover) {
    # rqt caches the plugin list, so a newly built plugin can stay invisible.
    $rqtArgs += '--force-discover'
}

$display = Get-DisplayTarget $XDisplay
$launchCommand = @"
source /opt/ros/humble/setup.bash
if [ -f $RaspicatWorkspace/install/setup.bash ]; then
  source $RaspicatWorkspace/install/setup.bash
fi
exec rqt $($rqtArgs -join ' ') >/tmp/rqt.log 2>&1
"@

podman exec -d -e "DISPLAY=$display" $Container bash -lc $launchCommand
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to start rqt.'
}

Start-Sleep -Seconds 2
$rqtPid = podman exec $Container bash -lc "pgrep -f '$RqtPattern' | head -n 1" 2>$null
if (-not $rqtPid) {
    podman exec $Container bash -lc 'tail -n 50 /tmp/rqt.log 2>/dev/null || true'
    # A -Plugin that matches nothing is the likely cause; the log says so.
    # `rqt --list-plugins` in the container lists what it would accept.
    throw 'rqt exited during startup. The container log is shown above.'
}

Write-Host "rqt started (PID $($rqtPid.Trim()), DISPLAY=$display)."
Write-Host "Log: podman exec $Container tail -n 50 /tmp/rqt.log"
