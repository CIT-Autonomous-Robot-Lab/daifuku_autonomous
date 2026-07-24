[CmdletBinding()]
param(
    [ValidateSet('List', 'Nodes', 'Info', 'Echo', 'Hz')]
    [string]$Action = 'List',
    [string]$Topic = '',
    [System.Net.IPAddress]$PiAddress,
    [string]$EthernetAlias = '',
    [ValidateRange(0, 232)]
    [int]$DomainId = 90,
    [string]$User = 'ubuntu'
)

$ErrorActionPreference = 'Stop'

if ($Action -in @('Info', 'Echo', 'Hz')) {
    if (-not $Topic) { throw "-$Action requires -Topic, for example -Topic /odom." }
    if ($Topic -notmatch '^/[A-Za-z0-9_/]+$') { throw "Invalid ROS topic name: $Topic" }
}

$sshCommand = (Get-Command ssh -ErrorAction Stop).Source
$candidateSet = [Collections.Generic.HashSet[string]]::new()

function Add-Candidate {
    param([string]$Address)
    $parsed = $null
    if ([Net.IPAddress]::TryParse($Address, [ref]$parsed) -and
        $parsed.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork) {
        $octets = $parsed.GetAddressBytes()
        if ($octets[0] -eq 0 -or $octets[0] -ge 224 -or
            $octets[3] -eq 0 -or $octets[3] -eq 255) {
            return
        }
        [void]$candidateSet.Add($parsed.IPAddressToString)
    }
}

function Test-Raspicat {
    param([string]$Address)
    $remoteCheck = "bash -lc 'source /opt/ros/humble/setup.bash; export ROS_DOMAIN_ID=$DomainId; timeout 8 ros2 topic info /odom --no-daemon 2>/dev/null'"
    $arguments = @(
        '-o', 'BatchMode=yes',
        '-o', 'PasswordAuthentication=no',
        '-o', 'KbdInteractiveAuthentication=no',
        '-o', 'ConnectTimeout=3',
        '-o', 'StrictHostKeyChecking=accept-new',
        '-o', 'HostKeyAlias=raspicat-dynamic',
        "$User@$Address",
        $remoteCheck
    )
    $result = & $sshCommand @arguments 2>$null
    return ($LASTEXITCODE -eq 0 -and ($result -join "`n") -match 'Publisher count:\s*[1-9]')
}

function Find-SshHosts {
    param([string]$Prefix)
    $probes = @()
    foreach ($suffix in 2..254) {
        $address = "$Prefix.$suffix"
        $client = [Net.Sockets.TcpClient]::new()
        try {
            $asyncResult = $client.BeginConnect($address, 22, $null, $null)
            $probes += [pscustomobject]@{
                Address = $address
                Client = $client
                AsyncResult = $asyncResult
            }
        } catch {
            $client.Dispose()
        }
    }

    Start-Sleep -Milliseconds 2500
    foreach ($probe in $probes) {
        try {
            if ($probe.AsyncResult.IsCompleted -and $probe.Client.Connected) {
                $probe.Client.EndConnect($probe.AsyncResult)
                $probe.Address
            }
        } catch {
            # Ignore hosts that refused or closed the connection.
        } finally {
            $probe.Client.Dispose()
        }
    }
}

if ($PiAddress) {
    Add-Candidate $PiAddress.IPAddressToString
} else {
    foreach ($hostName in @('raspicat.local', 'ubuntu.local')) {
        try {
            $dnsTask = [Net.Dns]::GetHostAddressesAsync($hostName)
            if ($dnsTask.Wait(750)) {
                $dnsTask.Result |
                    Where-Object AddressFamily -eq InterNetwork |
                    ForEach-Object { Add-Candidate $_.IPAddressToString }
            }
        } catch {
            # mDNS is optional on Windows.
        }
    }

    $neighbors = Get-NetNeighbor -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object State -notin @('Unreachable', 'Incomplete')
    if ($EthernetAlias) {
        $adapter = Get-NetAdapter -Name $EthernetAlias -ErrorAction Stop
        $neighbors = $neighbors | Where-Object InterfaceIndex -eq $adapter.ifIndex
    }
    $neighbors | ForEach-Object { Add-Candidate $_.IPAddress }
}

$foundAddress = $null
foreach ($candidate in $candidateSet) {
    Write-Host "Checking $candidate ..."
    if (Test-Raspicat $candidate) {
        $foundAddress = $candidate
        break
    }
}

if (-not $foundAddress -and -not $PiAddress) {
    $hostAddress = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notlike '127.*' -and
            (-not $EthernetAlias -or $_.InterfaceAlias -eq $EthernetAlias)
        } |
        Sort-Object @{ Expression = { if ($_.IPAddress -eq '192.168.137.1') { 0 } else { 1 } } } |
        Select-Object -First 1
    if (-not $hostAddress) {
        throw 'Could not determine the wired IPv4 subnet. Pass -PiAddress or -EthernetAlias.'
    }

    $prefix = $hostAddress.IPAddress -replace '\.\d+$', ''
    Write-Host "No cached neighbor matched. Scanning $prefix.0/24 for SSH ..."
    foreach ($candidate in Find-SshHosts $prefix) {
        Write-Host "Checking $candidate ..."
        if (Test-Raspicat $candidate) {
            $foundAddress = $candidate
            break
        }
    }
}

if (-not $foundAddress) {
    throw 'Raspberry Pi Cat was not found. Confirm Ethernet, DHCP, SSH public-key login, and ROS_DOMAIN_ID.'
}

Write-Host "Raspberry Pi Cat: $User@$foundAddress (ROS_DOMAIN_ID=$DomainId)" -ForegroundColor Green

$rosCommand = switch ($Action) {
    'List'  { 'timeout 15 ros2 topic list --no-daemon' }
    'Nodes' { 'timeout 15 ros2 node list --no-daemon' }
    'Info'  { "timeout 15 ros2 topic info '$Topic' --verbose --no-daemon" }
    'Echo'  { "timeout 30 ros2 topic echo '$Topic' --once" }
    'Hz'    { "timeout 15 ros2 topic hz '$Topic' --window 20 || test `$? -eq 124" }
}
$remoteCommand = "bash -lc 'source /opt/ros/humble/setup.bash; export ROS_DOMAIN_ID=$DomainId; $rosCommand'"
$sshArguments = @(
    '-o', 'BatchMode=yes',
    '-o', 'PasswordAuthentication=no',
    '-o', 'KbdInteractiveAuthentication=no',
    '-o', 'ConnectTimeout=5',
    '-o', 'StrictHostKeyChecking=accept-new',
    '-o', 'HostKeyAlias=raspicat-dynamic',
    "$User@$foundAddress",
    $remoteCommand
)
& $sshCommand @sshArguments
if ($LASTEXITCODE -ne 0) { throw "Remote ROS command failed with exit code $LASTEXITCODE." }
