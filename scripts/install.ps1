#Requires -Version 5.1
<# First install ONLY. Import a new distro, apply a pinned public GitHub flake.
No private keys, portproxy/firewall changes, unregister or global shutdown. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^github:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/[a-f0-9]{40}#[a-z][a-z0-9-]*$')]
    [string]$Flake,
    [Parameter(Mandatory = $true)][string]$ImagePath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ImageSha256,
    [ValidatePattern('^[A-Za-z][A-Za-z0-9-]{0,63}$')][string]$Distribution = 'NixOS-Managed',
    [string]$InstallDirectory,
    [switch]$RestartDistribution
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Run on Windows from the account owning this WSL distro.' }
function Invoke-Native {
    param([string]$Command, [string[]]$Arguments)
    $output = & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command failed ($LASTEXITCODE). Distro retained; inspect before retrying." }
    return $output
}
function Assert-NoReparsePath {
    param([string]$Path)
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if ((Test-Path -LiteralPath $cursor) -and
            ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Symlink/reparse-point paths refused.'
        }
        $cursor = Split-Path -Parent $cursor
    }
}
$null = Get-Command wsl.exe -ErrorAction Stop
$architecture = $env:PROCESSOR_ARCHITEW6432
if (-not $architecture) { $architecture = $env:PROCESSOR_ARCHITECTURE }
if ($architecture -ne 'AMD64') { throw 'This base currently supports x86_64 Windows only.' }
$ImagePath = (Resolve-Path -LiteralPath $ImagePath).ProviderPath
Assert-NoReparsePath $ImagePath
if ((Get-Item -LiteralPath $ImagePath).PSIsContainer -or
    (Get-FileHash -LiteralPath $ImagePath -Algorithm SHA256).Hash -ine $ImageSha256) {
    throw 'Rootfs checksum/type mismatch. Nothing imported.'
}
$list = (Invoke-Native wsl.exe @('--list', '--quiet')) -join "`n"
$existing = @($list.Replace([string][char]0, '').Split("`n") | ForEach-Object { $_.Trim() })
if ($existing -contains $Distribution) { throw 'Distribution exists. Use SSH/nixos-rebuild to update; never overwrite it here.' }
if (-not $InstallDirectory) { $InstallDirectory = Join-Path $env:LOCALAPPDATA $Distribution }
$InstallDirectory = [IO.Path]::GetFullPath($InstallDirectory)
Assert-NoReparsePath $InstallDirectory
if ((Test-Path -LiteralPath $InstallDirectory) -and
    ((Get-Item -LiteralPath $InstallDirectory).PSIsContainer -eq $false -or
     @(Get-ChildItem -LiteralPath $InstallDirectory -Force).Count -gt 0)) {
    throw 'Install destination must be new or an empty directory.'
}
$null = Invoke-Native wsl.exe @('--import', $Distribution, $InstallDirectory, $ImagePath, '--version', '2')
$guest = @'
set -euo pipefail
flake=$1
[[ $flake =~ ^github:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/[a-f0-9]{40}#[a-z][a-z0-9-]*$ ]]
[[ -e /etc/NIXOS ]] || { echo 'NixOS rootfs required.' >&2; exit 1; }
kernel=$(uname -r)
[[ ${kernel,,} == *microsoft*wsl2* ]] || { echo 'WSL2 kernel required.' >&2; exit 1; }
reference=${flake%#*}
host=${flake##*#}
attribute="$reference#nixosConfigurations.$host.config"
flags=(--extra-experimental-features 'nix-command flakes')
user=$(nix "${flags[@]}" eval --raw --no-write-lock-file "$attribute.wsl.defaultUser")
[[ $user =~ ^[a-z_][a-z0-9_-]{0,31}$ && $user != root && $user != nobody ]]
for option in wsl.enable services.openssh.enable services.openssh.settings.PubkeyAuthentication; do
  [[ $(nix "${flags[@]}" eval --json --no-write-lock-file "$attribute.$option") == true ]]
done
for option in services.openssh.settings.PasswordAuthentication services.openssh.settings.KbdInteractiveAuthentication; do
  [[ $(nix "${flags[@]}" eval --json --no-write-lock-file "$attribute.$option") == false ]]
done
[[ $(nix "${flags[@]}" eval --raw --no-write-lock-file "$attribute.services.openssh.settings.PermitRootLogin") == no ]]
[[ $(nix "${flags[@]}" eval --json --no-write-lock-file "$attribute.wslManagement.authorizedKeys") != '[]' ]]
# Nix builds contain only public configuration. No real credentials are needed.
system=$(nix "${flags[@]}" build --no-link --print-out-paths --no-write-lock-file "$attribute.system.build.toplevel")
[[ $system == /nix/store/* && $system != *$'\n'* ]]
nix-env --profile /nix/var/nix/profiles/system --set "$system"
"$system/bin/switch-to-configuration" switch
sshd -t
systemctl is-active sshd.service
install -d -m 700 /var/lib/nixos-wsl-base
printf '%s\n' "$flake" > /var/lib/nixos-wsl-base/bootstrap-ref
printf 'SSH user: %s\n' "$user"
echo 'Verify this HOST key fingerprint on the Linux client, using this Windows console as the trusted channel:'
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
printf 'Base installed. No age identity was created or copied. Guest SSH port(s): '
nix "${flags[@]}" eval --json --no-write-lock-file "$attribute.services.openssh.ports"
echo
'@
$oldEncoding = $OutputEncoding
try {
    $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $guest.Replace("`r`n", "`n") | & wsl.exe --distribution $Distribution --user root --exec /bin/bash -s -- $Flake
    if ($LASTEXITCODE -ne 0) { throw 'Guest build/activation failed. Distro retained; do not reimport/delete it blindly.' }
} finally { $OutputEncoding = $oldEncoding }
if ($RestartDistribution) {
    Write-Warning "Terminating only '$Distribution' to apply its WSL boot/default-user settings."
    $null = Invoke-Native wsl.exe @('--terminate', $Distribution)
}
Write-Host "Installed '$Distribution'. Open with: wsl -d $Distribution"
Write-Host 'Expose SSH separately with expose-ssh.ps1; no Windows network rules were changed by installation.'
