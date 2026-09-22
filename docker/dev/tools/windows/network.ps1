param(
    [ValidateSet('Static', 'Disable')]
    [string]$Mode = 'Static',
    [string]$EthernetAlias = '',
    # コマンドラインの互換のために残してある (固定 IP では NAT も外向きの
    # アダプタも使わない)。
    [string]$InternetAlias = '',
    [string]$RobotHostAddress = '192.168.1.1',
    [ValidateRange(1, 32)]
    [int]$RobotPrefixLength = 24
)

$ErrorActionPreference = 'Stop'

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an Administrator PowerShell session.'
}

if (-not $EthernetAlias) {
    $upEthernet = @(Get-NetAdapter -Physical | Where-Object {
        $_.Status -eq 'Up' -and $_.MediaType -eq '802.3'
    })
    if ($upEthernet.Count -ne 1) {
        throw "Could not choose one wired adapter. Pass -EthernetAlias. Candidates: $($upEthernet.Name -join ', ')"
    }

    $EthernetAlias = $upEthernet[0].Name

    # 物理アダプタが Hyper-V の外部スイッチに繋がっているときは、ホストのアドレスを
    # 管理 OS 側の vEthernet アダプタへ振る。
    $externalSwitch = Get-VMSwitch -SwitchType External -ErrorAction SilentlyContinue |
        Where-Object NetAdapterInterfaceDescription -eq $upEthernet[0].InterfaceDescription |
        Select-Object -First 1
    if ($externalSwitch) {
        $managementAdapter = Get-VMNetworkAdapter -ManagementOS -SwitchName $externalSwitch.Name |
            Select-Object -First 1
        if ($managementAdapter) {
            $managementAlias = "vEthernet ($($managementAdapter.Name))"
            if (Get-NetAdapter -Name $managementAlias -ErrorAction SilentlyContinue) {
                $EthernetAlias = $managementAlias
            }
        }
    }
}

$sharing = New-Object -ComObject HNetCfg.HNetShare
foreach ($connection in @($sharing.EnumEveryConnection())) {
    $config = $sharing.INetSharingConfigurationForINetConnection($connection)
    if ($config.SharingEnabled) { $config.DisableSharing() }
}

# 古い構成では ICS とは別に Open DHCP Server が入っていることがある。ファイルと
# 設定はそのままに、固定のロボット LAN でリースを配らせず、次回の起動でも上げない。
$openDhcp = Get-Service -Name 'OpenDHCPServer' -ErrorAction SilentlyContinue
if ($openDhcp) {
    if ($openDhcp.Status -ne 'Stopped') {
        Stop-Service -Name 'OpenDHCPServer' -Force
    }
    Set-Service -Name 'OpenDHCPServer' -StartupType Disabled
}

# 以前の ICS のサブネットを外す。Raspberry Pi Cat も Livox も 192.168.1.0/24 の
# 固定アドレスなので、このケーブルに DHCP も NAT も要らない。
Get-NetIPAddress `
    -InterfaceAlias $EthernetAlias `
    -AddressFamily IPv4 `
    -IPAddress '192.168.137.1' `
    -ErrorAction SilentlyContinue |
    Remove-NetIPAddress -Confirm:$false

if ($Mode -eq 'Disable') {
    Get-NetIPAddress `
        -InterfaceAlias $EthernetAlias `
        -AddressFamily IPv4 `
        -IPAddress $RobotHostAddress `
        -ErrorAction SilentlyContinue |
        Remove-NetIPAddress -Confirm:$false
    Write-Host "RasPiCat static network removed from $EthernetAlias; ICS/DHCP remains disabled."
    exit 0
}

if ($RobotHostAddress) {
    $robotAddress = Get-NetIPAddress `
        -InterfaceAlias $EthernetAlias `
        -AddressFamily IPv4 `
        -IPAddress $RobotHostAddress `
        -ErrorAction SilentlyContinue
    if (-not $robotAddress) {
        New-NetIPAddress `
            -InterfaceAlias $EthernetAlias `
            -IPAddress $RobotHostAddress `
            -PrefixLength $RobotPrefixLength | Out-Null
    }
}

Write-Host "Static RasPiCat network configured on $EthernetAlias."
Write-Host "Windows=$RobotHostAddress/$RobotPrefixLength, Podman=192.168.1.2, Pi=192.168.1.50, Livox=192.168.1.108."
Write-Host 'Windows ICS/DHCP/NAT is disabled for the robot LAN.'
