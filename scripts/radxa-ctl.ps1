#!/usr/bin/env pwsh
<#
.SYNOPSIS
Connect to a Radxa over public DNS or scan the LAN for it.

.EXAMPLE
.\radxa-ctl.ps1
.\radxa-ctl.ps1 -ScanLan
.\radxa-ctl.ps1 -HostName 192.168.10.52 -User root -RemoteCommand 'uptime'
#>
[CmdletBinding()]
param(
    [string]$HostName = '',
    [string]$Domain = 'lain42.top',
    [string]$User = 'root',
    [string]$Key = '',
    [int]$Port = 22,
    [switch]$PublicOnly,
    [switch]$ScanLan,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemoteCommand
)

$ErrorActionPreference = 'Stop'

if (-not $Key) {
    $Key = Join-Path $HOME '.ssh\lain42.pem'
}

function Get-PublicIp {
    param([string]$DomainName)
    $record = Resolve-DnsName $DomainName -Type A -ErrorAction Stop |
        Select-Object -First 1
    return $record.IPAddress
}

function Get-LanCandidates {
    $gw = $null
    try {
        $cfg = Get-NetIPConfiguration -ErrorAction Stop |
            Where-Object { $_.IPv4DefaultGateway -ne $null } |
            Select-Object -First 1
        $gw = $cfg.IPv4DefaultGateway.NextHop
    } catch {
        $ipconfig = ipconfig /all | Out-String
        if ($ipconfig -match 'Default Gateway .*: ([0-9.]+)') {
            $gw = $Matches[1]
        }
    }
    if (-not $gw) { return @() }

    $parts = $gw -split '\.'
    $prefix = ($parts[0..2] -join '.') + '.'
    Write-Host "Scanning ${prefix}0/24 ..."

    $online = 1..254 | ForEach-Object -Parallel {
        $ip = $using:prefix + $_
        if (Test-Connection -ComputerName $ip -Count 1 -Quiet -ErrorAction SilentlyContinue) {
            $ip
        }
    } -ThrottleLimit 64
    return @($online | Sort-Object { [int]($_ -replace '.*\.', '') })
}

function Test-SshHost {
    param([string]$Candidate)
    $known = Join-Path $HOME '.ssh\known_hosts'
    & ssh -i $Key `
        -o BatchMode=yes `
        -o StrictHostKeyChecking=accept-new `
        -o UserKnownHostsFile=$known `
        -o ConnectTimeout=5 `
        "$User@$Candidate" 'echo READY' 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

$target = $HostName
if (-not $target -and $ScanLan) {
    foreach ($candidate in (Get-LanCandidates)) {
        Write-Host "Trying LAN host $candidate ..."
        if (Test-SshHost $candidate) {
            $target = $candidate
            break
        }
    }
}

if (-not $target -and -not $PublicOnly) {
    # mDNS names are cheap to check before falling back to public DNS.
    foreach ($name in @('radxa.local', 'rock.local', 'debian.local', 'armbian.local')) {
        $resolved = Resolve-DnsName $name -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($resolved -and $resolved.IPAddress) {
            Write-Host "Found mDNS host $name => $($resolved.IPAddress)"
            $target = $resolved.IPAddress
            break
        }
    }
}

if (-not $target) {
    Write-Host "Resolving $Domain ..."
    $target = Get-PublicIp -DomainName $Domain
}

if (-not $target) {
    throw 'Unable to locate the Radxa host.'
}

Write-Host "Connecting ${User}@${target} ($Domain) with $Key"
& ssh -i $Key -p $Port `
    -o ConnectTimeout=12 `
    -o ServerAliveInterval=30 `
    -o ServerAliveCountMax=4 `
    -t "$User@$target" @RemoteCommand
