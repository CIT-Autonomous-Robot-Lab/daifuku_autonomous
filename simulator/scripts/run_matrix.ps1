<#
.SYNOPSIS
  pi4_sim のケース一式を順に回して PROBE_SUMMARY を集める。

.DESCRIPTION
  1 ケースあたり bringup + ゴール1回で数分かかる。結果は
  simulator/scripts/results/<timestamp>/ に保存し、最後に要約を表示する。

  ケースの意図:
    calib_navfn  CPU quota のキャリブレーション。実機で取れている唯一の
                 スループット実測 (navfn の planner ループが 20Hz 設定に対し
                 7.6Hz) に合わせる。地図は実機のまま (しきい値を直すと navfn の
                 問題規模が 1/4 になり別物になる)。
    baseline     実機と同じ設定 + Pi4 相当の制限。plan=0 の再現を狙う。
    nolimits     同じ設定を制限なしで。CPU/メモリが原因か、設定・地図が原因かを
                 切り分ける対照。
    free_thresh  未観測セルを free にしてしまっている地図しきい値だけ直す。
    map10cm      さらに地図を 0.10 m/cell に落とす (状態数 1/4)。
    map10cm_dwb  上に加えて狭域を DWB に戻し、VI プロセスを 1 つにする。
#>
[CmdletBinding()]
param(
    [string[]]$Only = @(),
    [int]$Quota = 6000,
    [string]$Memory = "3g",
    [string]$Image = "daifuku-autonomous:humble-amd64",
    [string]$Connection = "podman-machine-default-root"
)

$ErrorActionPreference = "Continue"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outDir = Join-Path $PSScriptRoot "results\$stamp"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$cases = @(
    @{ Name = "calib_navfn"; Limits = $true; Env = @{ PLANNER = "navfn"; PLANNER_EXPECTED_FREQ = "20" } },
    @{ Name = "baseline"; Limits = $true; Env = @{} },
    @{ Name = "nolimits"; Limits = $false; Env = @{} },
    @{ Name = "free_thresh"; Limits = $true; Env = @{ MAP_FREE_THRESH = "0.15" } },
    @{ Name = "map10cm"; Limits = $true; Env = @{ MAP_FREE_THRESH = "0.15"; MAP_SCALE = "2" } },
    @{ Name = "map10cm_dwb"; Limits = $true; Env = @{ MAP_FREE_THRESH = "0.15"; MAP_SCALE = "2"; LOCAL_PLANNER = "nav2" } }
)

foreach ($case in $cases) {
    if ($Only.Count -gt 0 -and $Only -notcontains $case.Name) { continue }
    $log = Join-Path $outDir "$($case.Name).log"
    Write-Host "=== $($case.Name) -> $log"
    $args = @{
        Case       = $case.Name
        CaseEnv    = $case.Env
        Quota      = $Quota
        Memory     = $Memory
        Image      = $Image
        Connection = $Connection
    }
    if ($case.Limits) {
        $args.Container = "pi4sim"
    }
    else {
        $args.Container = "pi4sim_full"
        $args.NoLimits = $true
    }
    & (Join-Path $PSScriptRoot "run_pi4_sim.ps1") @args 2>&1 | Tee-Object -FilePath $log
}

Write-Host "`n=== summary ==="
Get-ChildItem $outDir -Filter *.log | ForEach-Object {
    $line = (Select-String -Path $_.FullName -Pattern "PROBE_SUMMARY" | Select-Object -Last 1).Line
    $rate = (Select-String -Path $_.FullName -Pattern "current loop rate is" | Select-Object -Last 1).Line
    "--- $($_.BaseName)"
    if ($rate) { "    $rate" }
    if ($line) { "    $line" } else { "    (no PROBE_SUMMARY)" }
}
Write-Host "results in $outDir"
