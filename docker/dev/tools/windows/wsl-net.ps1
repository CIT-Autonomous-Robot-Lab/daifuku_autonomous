# WSL2 の VM をロボット LAN (bridged) と既定の NAT とで切り替える。bridged は WSL を
# 192.168.1.0/24 に置いて DDS を機体まで通すが、**外部スイッチの上流が要る** —
# USB GbE を抜くと DNS を含めて全部の通信が落ちる ("Temporary failure in name
# resolution")。docs/setup/network.md。
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('Bridged', 'Nat', 'Status')]
    [string]$Mode = 'Status'
)

$ErrorActionPreference = 'Stop'

$configPath = Join-Path $env:USERPROFILE '.wslconfig'
if (-not (Test-Path $configPath)) { throw "$configPath not found." }

# bridged を作る 3 つのキー。コメントアウトすれば NAT へ戻る。WSL は bridged の外では
# vmSwitch / dhcp を無視するが、残したまま mirrored で踏んだことがあるので 3 つとも隠す。
$keys = 'networkingMode', 'vmSwitch', 'dhcp'
$lines = Get-Content $configPath
$isBridged = $lines | Where-Object { $_ -match '^\s*networkingMode\s*=\s*bridged\s*$' }

if ($Mode -eq 'Status') {
    $running = (wsl.exe -d Ubuntu-22.04 -- wslinfo --networking-mode) 2>$null
    Write-Host ".wslconfig: $(if ($isBridged) { 'bridged' } else { 'nat' }), running VM: $running"
    return
}

$want = $Mode -eq 'Bridged'
if ($want -eq [bool]$isBridged) {
    Write-Host "Already $($Mode.ToLower()); nothing to do."
    return
}

$lines = $lines | ForEach-Object {
    $key = ($_ -replace '^\s*#\s*', '') -split '=' | Select-Object -First 1
    if ($key.Trim() -notin $keys) { return $_ }
    if ($want) { $_ -replace '^\s*#\s*', '' } else { '#' + ($_ -replace '^\s*#\s*', '') }
}
Set-Content -Path $configPath -Value $lines

Write-Host "Switched .wslconfig to $($Mode.ToLower()); restarting the WSL VM."
wsl.exe --shutdown
Write-Host 'Done. Start a distro to apply.'
