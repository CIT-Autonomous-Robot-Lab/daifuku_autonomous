# Inspect ROS 2 state on Raspberry Pi Cat over SSH, without starting DDS on
# the Windows host. The robot LAN uses fixed addresses, so no discovery is
# performed; pass -PiAddress if the robot is somewhere else.
[CmdletBinding()]
param(
    [ValidateSet('List', 'Nodes', 'Info', 'Echo', 'Hz')]
    [string]$Action = 'List',
    [string]$Topic = '',
    [System.Net.IPAddress]$PiAddress = '192.168.1.50',
    [ValidateRange(0, 232)]
    [int]$DomainId = 90,
    [string]$User = 'ubuntu'
)

$ErrorActionPreference = 'Stop'

if ($Action -in @('Info', 'Echo', 'Hz')) {
    if (-not $Topic) { throw "-Action $Action requires -Topic, for example -Topic /odom." }
    if ($Topic -notmatch '^/[A-Za-z0-9_/]+$') { throw "Invalid ROS topic name: $Topic" }
}

$rosCommand = switch ($Action) {
    'List'  { 'timeout 15 ros2 topic list --no-daemon' }
    'Nodes' { 'timeout 15 ros2 node list --no-daemon' }
    'Info'  { "timeout 15 ros2 topic info '$Topic' --verbose --no-daemon" }
    'Echo'  { "timeout 30 ros2 topic echo '$Topic' --once" }
    'Hz'    { "timeout 15 ros2 topic hz '$Topic' --window 20 || test `$? -eq 124" }
}

Write-Host "Raspberry Pi Cat: $User@$PiAddress (ROS_DOMAIN_ID=$DomainId)" -ForegroundColor Green

$sshArguments = @(
    '-o', 'BatchMode=yes',
    '-o', 'PasswordAuthentication=no',
    '-o', 'KbdInteractiveAuthentication=no',
    '-o', 'ConnectTimeout=5',
    '-o', 'StrictHostKeyChecking=accept-new',
    "$User@$PiAddress",
    "bash -lc 'source /opt/ros/humble/setup.bash; export ROS_DOMAIN_ID=$DomainId; $rosCommand'"
)

ssh @sshArguments
if ($LASTEXITCODE -ne 0) {
    throw "Remote ROS command failed with exit code $LASTEXITCODE. Check the Ethernet cable, the fixed IP $PiAddress, SSH public-key login, and ROS_DOMAIN_ID=$DomainId."
}
