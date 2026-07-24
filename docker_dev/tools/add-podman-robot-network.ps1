#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$MachineName = 'podman-hyperv',
    [string]$PodmanConnection = 'podman-hyperv',
    [string]$EthernetAlias = 'Ethernet',
    [string]$SwitchName = 'RasPiCat External',
    [string]$AdapterName = 'RasPiCat LAN',
    [string]$VmInterface = 'raspi0',
    [string]$VmAddress = '192.168.1.2/24'
)

$ErrorActionPreference = 'Stop'

$physicalAdapter = Get-NetAdapter -Name $EthernetAlias
if ($physicalAdapter.Status -ne 'Up') {
    throw "Network adapter '$EthernetAlias' is not connected."
}

$switch = Get-VMSwitch -Name $SwitchName -ErrorAction SilentlyContinue
if (-not $switch) {
    Write-Host "Creating external Hyper-V switch '$SwitchName' on '$EthernetAlias'..."
    $switch = New-VMSwitch `
        -Name $SwitchName `
        -NetAdapterName $EthernetAlias `
        -AllowManagementOS $true
}
elseif ($switch.SwitchType -ne 'External') {
    throw "A non-external switch named '$SwitchName' already exists."
}

$vmAdapter = Get-VMNetworkAdapter -VMName $MachineName -Name $AdapterName -ErrorAction SilentlyContinue
if (-not $vmAdapter) {
    Write-Host "Adding '$AdapterName' to VM '$MachineName'..."
    Add-VMNetworkAdapter -VMName $MachineName -Name $AdapterName -SwitchName $SwitchName
} elseif ($vmAdapter.SwitchName -ne $SwitchName) {
    Connect-VMNetworkAdapter -VMName $MachineName -Name $AdapterName -SwitchName $SwitchName
}

Write-Host 'Hyper-V network configuration:'
Get-VMNetworkAdapter -VMName $MachineName |
    Format-Table Name, SwitchName, MacAddress, IPAddresses -AutoSize

$connections = @(podman system connection list --format json | ConvertFrom-Json)
$connection = $connections | Where-Object Name -eq $PodmanConnection | Select-Object -First 1
if (-not $connection) {
    throw "Podman connection '$PodmanConnection' was not found."
}

$connectionUri = [uri]$connection.URI
$sshTarget = '{0}@{1}' -f $connectionUri.UserInfo, $connectionUri.Host
$remoteCommand = @"
set -eu
if ! ip link show '$VmInterface' >/dev/null 2>&1; then
  current_interface='eth0'
  connection_name=`$(nmcli -g GENERAL.CONNECTION device show "`$current_interface")
  mac=`$(cat "/sys/class/net/`$current_interface/address")
  sudo nmcli connection modify "`$connection_name" connection.interface-name '$VmInterface'
  printf 'SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="%s", NAME="$VmInterface"\n' "`$mac" | \
    sudo tee /etc/udev/rules.d/70-raspicat-lan.rules >/dev/null
  sudo ip link set "`$current_interface" down
  sudo ip link set "`$current_interface" name '$VmInterface'
  sudo ip link set '$VmInterface' up
fi
connection_name=`$(nmcli -g GENERAL.CONNECTION device show '$VmInterface')
sudo nmcli connection modify "`$connection_name" \
  ipv4.method manual ipv4.addresses '$VmAddress' ipv4.gateway '' \
  ipv4.never-default yes ipv4.dns '' ipv6.method disabled
sudo nmcli connection up "`$connection_name"
ip -brief address show '$VmInterface'
"@

Write-Host "Assigning $VmAddress to $MachineName/$VmInterface..."
& ssh `
    -i $connection.Identity `
    -p $connectionUri.Port `
    -o BatchMode=yes `
    -o StrictHostKeyChecking=accept-new `
    $sshTarget $remoteCommand
if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure $VmInterface in Podman machine '$MachineName'."
}
