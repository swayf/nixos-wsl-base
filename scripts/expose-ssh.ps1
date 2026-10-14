#Requires -Version 5.1
#Requires -RunAsAdministrator
<# Explicit NAT-only exposure. One owned endpoint, explicit client IPv4 allowlist.
Rerun after WSL IP changes. Does not change networkingMode, unrelated rules or VPN.
A -WhatIf still queries/starts the selected WSL distro; it changes no network rules. #>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidatePattern('^[A-Za-z][A-Za-z0-9-]{0,63}$')][string]$Distribution = 'NixOS-Managed',
    [Parameter(Mandatory = $true)][string]$ListenAddress,
    [ValidateRange(1, 65535)][int]$ListenPort = 2222,
    [ValidateRange(1, 65535)][int]$GuestPort = 22,
    [string[]]$AllowedRemoteAddress = @(),
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Run in Windows PowerShell as administrator.' }
function Invoke-Native {
    param([string]$Command, [string[]]$Arguments)
    $output = & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command failed ($LASTEXITCODE)." }
    return $output
}
function Test-UsableIPv4 {
    param([string]$Address)
    $parsed = $null
    if ($Address -notmatch '^\d{1,3}(\.\d{1,3}){3}$' -or
        -not [Net.IPAddress]::TryParse($Address, [ref]$parsed) -or
        $parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { return $false }
    $bytes = $parsed.GetAddressBytes()
    return $bytes[0] -ne 0 -and $bytes[0] -ne 127 -and $bytes[0] -lt 224
}
if (-not (Test-UsableIPv4 $ListenAddress)) { throw 'Require one real Windows LAN/VPN IPv4, never a wildcard/loopback.' }
if (-not $Remove) {
    if ($AllowedRemoteAddress.Count -eq 0) { throw 'Require explicit Linux-client IPv4 addresses/CIDRs; no broad default.' }
    foreach ($range in $AllowedRemoteAddress) {
        $parts = $range.Split('/')
        if ($parts.Count -gt 2 -or -not (Test-UsableIPv4 $parts[0]) -or
            ($parts.Count -eq 2 -and ($parts[1] -notmatch '^\d{1,2}$' -or [int]$parts[1] -lt 8 -or [int]$parts[1] -gt 32))) {
            throw 'Only explicit usable IPv4 addresses or /8../32 CIDRs are accepted. Prefer client /32 addresses.'
        }
    }
}
$owner = "NixOS-WSL-$Distribution-$($ListenAddress.Replace('.', '-'))-$ListenPort"
$stateDirectory = Join-Path $env:ProgramData 'nixos-wsl-base'
$statePath = Join-Path $stateDirectory "$owner.json"
$cursor = [IO.Path]::GetFullPath($statePath)
while ($cursor) {
    if ((Test-Path -LiteralPath $cursor) -and
        ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Symlinked ownership state refused.'
    }
    $cursor = Split-Path -Parent $cursor
}
$state = $null
if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if ($state.owner -cne $owner -or $state.distribution -cne $Distribution -or
        $state.listenAddress -cne $ListenAddress -or $state.listenPort -ne $ListenPort -or
        -not (Test-UsableIPv4 $state.targetAddress) -or $state.targetPort -lt 1 -or $state.targetPort -gt 65535) {
        throw 'Invalid endpoint ownership record; do not overwrite it.'
    }
}
$proxyText = (Invoke-Native -Command netsh.exe -Arguments @('interface', 'portproxy', 'show', 'v4tov4')) -join "`n"
$entries = @()
foreach ($line in $proxyText.Split("`n")) {
    if ($line -match '^\s*(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s*$') {
        $entries += [PSCustomObject]@{ address = $Matches[1]; port = [int]$Matches[2]; target = $Matches[3]; targetPort = [int]$Matches[4] }
    }
}
if (@($entries | Where-Object { $_.address -eq '0.0.0.0' -and $_.port -eq $ListenPort }).Count) {
    throw 'Wildcard portproxy listener conflicts with this endpoint. Do not reset unrelated listeners.'
}
$current = @($entries | Where-Object { $_.address -eq $ListenAddress -and $_.port -eq $ListenPort })
$rule = @(Get-NetFirewallRule -Name $owner -ErrorAction SilentlyContinue)
if (($current.Count -gt 0 -or $rule.Count -gt 0) -and -not $state) {
    throw 'Endpoint/rule already exists without our ownership record; refusing takeover.'
}
if ($current.Count -gt 1 -or ($current.Count -eq 1 -and
    ($current[0].target -cne $state.targetAddress -or $current[0].targetPort -ne $state.targetPort))) {
    throw 'Existing portproxy differs from our ownership record; refusing overwrite.'
}
$deleteArgs = @('interface', 'portproxy', 'delete', 'v4tov4', "listenaddress=$ListenAddress", "listenport=$ListenPort")
if ($Remove) {
    if (-not $state) { throw 'No owned endpoint to remove.' }
    if ($PSCmdlet.ShouldProcess("$ListenAddress`:$ListenPort", 'Remove only the owned WSL endpoint/rule')) {
        $rule | Remove-NetFirewallRule
        if ($current.Count) { $null = Invoke-Native -Command netsh.exe -Arguments $deleteArgs }
        Remove-Item -LiteralPath $statePath
    }
    return
}
$null = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $ListenAddress -ErrorAction Stop
$profiles = @(Get-NetFirewallProfile)
if (@($profiles | Where-Object { $_.Enabled -ne $true -or $_.DefaultInboundAction -eq 'Allow' }).Count) {
    throw 'Require enabled default-deny Windows firewall profiles; do not disable the firewall.'
}
$occupied = @(Get-NetTCPConnection -State Listen -LocalPort $ListenPort -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalAddress -in @($ListenAddress, '0.0.0.0', '::') })
if ($occupied.Count -gt 0 -and $current.Count -eq 0) { throw 'Port is occupied by an unrelated listener.' }
# Do not expose a different OS just because it also has an SSH listener.
$null = Invoke-Native -Command wsl.exe -Arguments @('--distribution', $Distribution, '--user', 'root', '--exec',
    '/bin/sh', '-c', 'test -e /etc/NIXOS')
# eth0 only: hostname -I may include Podman bridges. This query starts the distro.
$addressJson = (Invoke-Native -Command wsl.exe -Arguments @('--distribution', $Distribution, '--user', 'root', '--exec',
    'ip', '-4', '-j', 'addr', 'show', 'dev', 'eth0')) -join "`n"
$guestIP = (($addressJson | ConvertFrom-Json).addr_info |
    Where-Object { $_.family -eq 'inet' } | Select-Object -First 1).local
if (-not (Test-UsableIPv4 $guestIP)) { throw 'No usable WSL eth0 IPv4.' }
if (Get-NetIPAddress -AddressFamily IPv4 -IPAddress $guestIP -ErrorAction SilentlyContinue) {
    throw 'WSL address belongs to Windows; this script is NAT-only, not mirrored mode.'
}
if (-not (Test-NetConnection -ComputerName $guestIP -Port $GuestPort -InformationLevel Quiet)) {
    throw 'Guest SSH is unreachable. No forwarding/firewall changes made.'
}
if (-not $PSCmdlet.ShouldProcess("$ListenAddress`:$ListenPort -> $guestIP`:$GuestPort", 'Expose SSH only to explicit Linux-client IPv4 allowlist')) { return }
$null = New-Item -ItemType Directory -Path $stateDirectory -Force
# Ownership records must not be writable by ordinary Windows users.
$null = Invoke-Native -Command icacls.exe -Arguments @($stateDirectory, '/inheritance:r', '/grant:r',
    '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F')
Start-Service iphlpsvc
# Fail closed while updating. Never flush the portproxy table or firewall.
$rule | Remove-NetFirewallRule
if ($current.Count) { $null = Invoke-Native -Command netsh.exe -Arguments $deleteArgs }
$added = $false
$temporaryState = Join-Path $stateDirectory ($owner + '.' + [guid]::NewGuid().ToString('N') + '.tmp')
try {
    $null = Invoke-Native -Command netsh.exe -Arguments @('interface', 'portproxy', 'add', 'v4tov4',
        "listenaddress=$ListenAddress", "listenport=$ListenPort", "connectaddress=$guestIP", "connectport=$GuestPort")
    $added = $true
    New-NetFirewallRule -Name $owner -DisplayName $owner -Direction Inbound -Action Allow -Protocol TCP `
        -LocalAddress $ListenAddress -LocalPort $ListenPort -RemoteAddress $AllowedRemoteAddress -Profile Any | Out-Null
    $record = @{ owner = $owner; distribution = $Distribution; listenAddress = $ListenAddress; listenPort = $ListenPort;
        targetAddress = $guestIP; targetPort = $GuestPort; allowedRemoteAddress = $AllowedRemoteAddress }
    $record | ConvertTo-Json | Set-Content -LiteralPath $temporaryState -Encoding UTF8
    Move-Item -LiteralPath $temporaryState -Destination $statePath -Force
} catch {
    Get-NetFirewallRule -Name $owner -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    if ($added) { $null = Invoke-Native -Command netsh.exe -Arguments $deleteArgs }
    Remove-Item -LiteralPath $temporaryState -Force -ErrorAction SilentlyContinue
    throw
}
Write-Host "WSL SSH: $ListenAddress`:$ListenPort -> $guestIP`:$GuestPort; clients: $($AllowedRemoteAddress -join ', ')"
Write-Host 'Rerun after WSL IP changes. Keep the distro running. Existing broader firewall allow rules may also permit traffic: audit them separately.'
