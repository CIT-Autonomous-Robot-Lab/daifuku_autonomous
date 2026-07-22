param(
    [ValidateSet('Enable', 'Disable')]
    [string]$Mode = 'Enable',
    [string]$EthernetAlias = '',
    [string]$InternetAlias = ''
)

$ErrorActionPreference = 'Stop'

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an Administrator PowerShell session.'
}

$sharing = New-Object -ComObject HNetCfg.HNetShare
$connections = @($sharing.EnumEveryConnection())
$items = foreach ($connection in $connections) {
    $properties = $sharing.NetConnectionProps($connection)
    [pscustomobject]@{ Connection = $connection; Name = $properties.Name; Device = $properties.DeviceName }
}

if (-not $EthernetAlias) {
    $upEthernet = @(Get-NetAdapter -Physical | Where-Object {
        $_.Status -eq 'Up' -and $_.MediaType -eq '802.3'
    })
    if ($upEthernet.Count -ne 1) {
        throw "Could not choose one wired adapter. Pass -EthernetAlias. Candidates: $($upEthernet.Name -join ', ')"
    }
    $EthernetAlias = $upEthernet[0].Name
}

$privateItem = $items | Where-Object Name -eq $EthernetAlias | Select-Object -First 1
if (-not $privateItem) { throw "Adapter '$EthernetAlias' was not found by Internet Connection Sharing." }

if ($Mode -eq 'Disable') {
    foreach ($item in $items) {
        $config = $sharing.INetSharingConfigurationForINetConnection($item.Connection)
        if ($config.SharingEnabled) { $config.DisableSharing() }
    }
    Write-Host "ICS/DHCP disabled for $EthernetAlias."
    exit 0
}

if (-not $InternetAlias) {
    $defaultRoute = Get-NetRoute -DestinationPrefix '0.0.0.0/0' |
        Sort-Object RouteMetric, InterfaceMetric | Select-Object -First 1
    if (-not $defaultRoute) { throw 'No default IPv4 Internet route was found. Pass -InternetAlias.' }
    $InternetAlias = (Get-NetAdapter -InterfaceIndex $defaultRoute.InterfaceIndex).Name
}
if ($InternetAlias -eq $EthernetAlias) { throw 'Internet and Raspberry Pi adapters must be different.' }

$publicItem = $items | Where-Object Name -eq $InternetAlias | Select-Object -First 1
if (-not $publicItem) { throw "Internet adapter '$InternetAlias' was not found by Internet Connection Sharing." }

# ICS owns its DHCP/NAT configuration. Windows normally uses 192.168.137.1/24.
foreach ($item in $items) {
    $config = $sharing.INetSharingConfigurationForINetConnection($item.Connection)
    if ($config.SharingEnabled) { $config.DisableSharing() }
}
$sharing.INetSharingConfigurationForINetConnection($publicItem.Connection).EnableSharing(0)
$sharing.INetSharingConfigurationForINetConnection($privateItem.Connection).EnableSharing(1)

Write-Host "Windows ICS/DHCP enabled: $InternetAlias -> $EthernetAlias."
Write-Host 'The wired host address is normally 192.168.137.1; the robot receives a 192.168.137.x lease.'
