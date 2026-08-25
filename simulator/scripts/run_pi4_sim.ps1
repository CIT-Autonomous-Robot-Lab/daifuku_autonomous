<#
.SYNOPSIS
  Raspberry Pi 4 (4GB) 相当に絞ったコンテナで Nav2 スタック + 疑似ロボットを
  走らせ、実機と同じゴールプローブを取る。

.DESCRIPTION
  実機が落ちている間に「Pi4 の何が効いているのか」をローカルで切り分けるための
  ハーネス。Pi4 を QEMU で命令エミュレーションはしない (aarch64 の qemu-user は
  減速率が読めず、rclrs/DDS のアトミック周りで別の問題を持ち込む。Dockerfile 自体
  arm64-under-QEMU のビルド回避策を抱えている)。代わりに:

    * CPU  : --cpuset-cpus 0-3 で 4 コアだけ見せ、cgroup の cpu.max で合計
             スループットを絞る。period を既定の 100ms ではなく 10ms にして、
             スロットリングの粒度を細かく (= 1回の停止を短く) する。
    * メモリ: --memory で制限。Pi4 は 4GB で、そのうち OS とコンテナ外の ROS
             ノード (raspicat ドライバ, robot_state_publisher, livox,
             pointcloud_to_laserscan, restamp, filter chain) が 0.5-1GB を
             使うため、コンテナが実際に使えるのは 3GB 程度。スワップ無し
             (--memory-swap = --memory) も実機に合わせる。

  既知の限界 (このハーネスで測れないもの):
    * cgroup の quota は「合計スループット」しか絞らない。単一スレッドの
      レイテンシは実機より速いままなので、ディスカバリ・bond・コールバック遅延と
      いった直列パスは楽観的に出る。並列 VI ソルバのような多スレッド負荷と
      メモリ制約は比較的よく再現できる。
    * コンテナ外 (実機ホスト側) の ROS ノード負荷は含まない。その分は quota を
      「Pi4 4 コア分」ではなく「nav2 が実際に取れた分」に絞ることで代用する。

  podman の既定接続は Hyper-V マシン (Fedora CoreOS) で、Windows のパスを
  bind mount できない (/mnt/c が無い)。そのため必要なファイルは podman cp で
  流し込む。

.PARAMETER Quota
  cpu.max の quota [us] (period=10000us に対する値)。6000 = 合計 0.6 コア。

.EXAMPLE
  .\run_pi4_sim.ps1 -Case baseline
  .\run_pi4_sim.ps1 -Case old_map -CaseEnv @{ MAP_FREE_THRESH = "0.25" }
#>
[CmdletBinding()]
param(
    [string]$Case = "baseline",
    [hashtable]$CaseEnv = @{},
    [int]$Quota = 6000,
    [int]$Period = 10000,
    [string]$Cpuset = "0-3",
    [string]$Memory = "3g",
    [string]$Image = "daifuku-autonomous:humble-amd64",
    [string]$Container = "pi4sim",
    # 既定の podman 接続は Hyper-V マシン (4GB 固定・管理者権限が無いと拡張不可)。
    # メモリ制約こそが検証対象なので、20GB 取れる WSL マシン側で回す。
    [string]$Connection = "podman-machine-default-root",
    [int]$DomainId = 91,
    [switch]$Recreate,
    [switch]$NoLimits,
    [switch]$SetupOnly
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$proj = Join-Path $repo "simulator"
$pkg = Join-Path $repo "src\daifuku_stack"
$share = "/opt/ros_ws/install/share/daifuku_stack"

$c = @()
if ($Connection) { $c = @("-c", $Connection) }

$exists = (podman @c ps -a --format "{{.Names}}") -contains $Container
if ($exists -and $Recreate) {
    podman @c rm -f $Container | Out-Null
    $exists = $false
}

if (-not $exists) {
    $limits = @()
    if (-not $NoLimits) {
        $limits = @(
            "--cpuset-cpus", $Cpuset,
            "--cpu-period", "$Period",
            "--cpu-quota", "$Quota",
            "--memory", $Memory,
            "--memory-swap", $Memory
        )
    }
    Write-Host "creating container $Container (limits: $($limits -join ' '))"
    podman @c run -d --name $Container `
        @limits `
        --shm-size 512m `
        -e ROS_DOMAIN_ID=$DomainId `
        -e ROS_LOCALHOST_ONLY=0 `
        -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp `
        -e FASTRTPS_DEFAULT_PROFILES_FILE=/etc/fastdds/local.xml `
        -e HOME=/tmp -e ROS_HOME=/tmp/ros -e ROS_LOG_DIR=/tmp/ros/log `
        --entrypoint /bin/bash `
        $Image -lc "sleep infinity" | Out-Null
}

# --- ホストの編集内容をコンテナへ流し込む (bind mount が使えないため) ---
# コンテナ内で走るものは simulator/container/ にまとまっている (Isaac 版の
# run_isaac_case.sh も同じディレクトリから同じ /opt/sim へ配る)。
$container_dir = Join-Path $proj "container"
podman @c exec $Container bash -lc "mkdir -p /opt/sim /etc/fastdds" | Out-Null
foreach ($f in Get-ChildItem (Join-Path $container_dir "*") -Include *.py, *.sh, *.xml) {
    podman @c cp $f.FullName "${Container}:/opt/sim/$($f.Name)" | Out-Null
}
# downsample_map.py だけは uv パッケージ側にある (ホストでも uv run downsample-map
# として使うため)。run_case.sh が MAP_SCALE 指定時にコンテナ内で呼ぶので、
# 単体スクリプトとしてここへも配る (intra-package import は持たない)。
podman @c cp (Join-Path $proj "src\daifuku_sim\downsample_map.py") "${Container}:/opt/sim/downsample_map.py" | Out-Null
podman @c cp (Join-Path $container_dir "fastdds_local.xml") "${Container}:/etc/fastdds/local.xml" | Out-Null
# podman cp は「足す」だけで消さないので、リネーム・移動したファイルが
# コンテナ側に残り続ける (config/ を分割したときの旧 nav2_params.yaml など)。
# 読まれはしないが紛らわしいので、上書き前に消しておく。
podman @c exec $Container bash -lc "rm -rf $share/config $share/scripts" | Out-Null
foreach ($d in "behavior_trees", "config", "launch", "maps", "rviz", "src") {
    $src = Join-Path $pkg $d
    if (Test-Path $src) { podman @c cp $src "${Container}:${share}/" | Out-Null }
}

# Windows の CRLF がそのまま渡ると bash / python が壊れるので落とす。
# **1 行で渡すこと。** here-string (@'...'@) はこの .ps1 自身の改行を
# そのまま bash へ渡すので、Windows チェックアウト (CRLF) では
# `syntax error near unexpected token $'do\r'` になる —— つまり
# **CRLF を落とすためのこのコマンド自身が CRLF で落ちる**。
$strip = 'for f in /opt/sim/*.py /opt/sim/*.sh; do [ -e "$f" ] && sed -i "s/\r$//" "$f"; done; chmod +x /opt/sim/*.sh'
podman @c exec $Container bash -lc $strip | Out-Null

if ($SetupOnly) { Write-Host "container ready: $Container"; exit 0 }

$envArgs = @()
foreach ($k in $CaseEnv.Keys) { $envArgs += @("-e", "$k=$($CaseEnv[$k])") }
$envArgs += @("-e", "CASE=$Case")

Write-Host "=== running case '$Case' ==="
podman @c exec @envArgs $Container bash /opt/sim/run_case.sh
exit $LASTEXITCODE
