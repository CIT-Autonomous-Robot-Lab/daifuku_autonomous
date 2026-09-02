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

# rqt は python スクリプトなのでプロセス名は python3 で、`pgrep -x rqt` は当たらない
# (引数のほうを見る)。角括弧はパターンを運ぶ `bash -lc` 自身に当たらないため、末尾の
# アンカーは /bin/rqt_plot まで拾わないため — 無いと -Restart がそちらを殺す。
$RqtPattern = '[/]bin/rqt( |$)'

Set-PodmanConnection -Name $PodmanConnection

$running = podman inspect --format '{{.State.Running}}' $Container 2>$null
if ($LASTEXITCODE -ne 0 -or $running.Trim() -ne 'true') {
    throw "Container '$Container' is not running. Run .\docker\dev\tools\windows\up.ps1 first."
}

if (-not (Start-XServer $XDisplay)) {
    throw "No X server is listening on TCP $(Get-XPort $XDisplay). Install VcXsrv at 'C:\Program Files\VcXsrv\vcxsrv.exe' or start an X server yourself."
}

# daifuku_rqt を載せているのはワークスペースのオーバーレイ。RViz と違って
# バインドマウントが落ちたときにホストから渡せる 1 ファイルが無いので、プラグイン
# 抜きで上げずに何が無いかを言う (-NoStandalone だとメニュー項目が消えるだけになる)。
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
    # rqt はプラグイン一覧を覚えているので、建てたばかりのものが見えないことがある。
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
    # -Plugin が何にも当たっていないのが大抵の原因 (ログに出る)。コンテナ内の
    # `rqt --list-plugins` が受け付ける名前を並べる。
    throw 'rqt exited during startup. The container log is shown above.'
}

Write-Host "rqt started (PID $($rqtPid.Trim()), DISPLAY=$display)."
Write-Host "Log: podman exec $Container tail -n 50 /tmp/rqt.log"
