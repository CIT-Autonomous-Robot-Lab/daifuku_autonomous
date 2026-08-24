# Switch the WSL2 VM between the robot LAN (bridged) and the default NAT.
# Bridged puts WSL on 192.168.1.0/24 so DDS reaches the robot, but it needs the
# external switch's uplink: unplug the USB GbE and WSL loses every network,
# including DNS (the symptom is "Temporary failure in name resolution").
# See docs/setup/network.md.
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('Bridged', 'Nat', 'Status')]
    [string]$Mode = 'Status'
)

$ErrorActionPreference = 'Stop'

$configPath = Join-Path $env:USERPROFILE '.wslconfig'
if (-not (Test-Path $configPath)) { throw "$configPath not found." }

# The three keys that make up the bridged position. Commenting them out is
# enough to fall back to NAT; WSL ignores vmSwitch/dhcp outside bridged mode,
# but leaving them uncommented has bitten mirrored before, so hide all three.
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
