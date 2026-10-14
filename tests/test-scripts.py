#!/usr/bin/env python3
"""Static, Bash and optional PowerShell/native-mock checks; never real WSL/network changes."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")

HARNESS = r'''
$ErrorActionPreference = 'Stop'
function Record($kind, $value) {
  [IO.File]::AppendAllText($env:TEST_LOG, (ConvertTo-Json -InputObject @{kind=$kind; value=$value} -Compress -Depth 8) + "`n")
}
$tokens = $null; $errors = $null
$null = [Management.Automation.Language.Parser]::ParseFile($env:TEST_ORIGINAL, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$env:OS='Windows_NT'; $env:PROCESSOR_ARCHITECTURE='AMD64'; $env:PROCESSOR_ARCHITEW6432=''
function wsl.exe {
  $call = @($args)
  Record 'wsl' $call
  $global:LASTEXITCODE=0
  if ($call[0] -eq '--list') {
    if ($env:TEST_CASE -eq 'existing') { "NixOS-Managed`0" }
    elseif ($env:TEST_CASE -eq 'list-failure') { $global:LASTEXITCODE=17 }
    else { "Unrelated-Ubuntu`0" }
  } elseif ($call[0] -eq '--import') {
    if ($env:TEST_CASE -eq 'import-failure') { $global:LASTEXITCODE=19 }
  } elseif ($call -contains '/bin/bash') {
    $body = $input | Out-String
    if ($body -notmatch 'set -euo pipefail' -or $body -notmatch 'switch-to-configuration') { throw 'Guest body missing' }
    if ($env:TEST_CASE -eq 'guest-failure') { $global:LASTEXITCODE=23 }
  } elseif ($call[0] -eq '--terminate') { }
  elseif ($call -contains '/bin/sh') {
    if ($env:TEST_CASE -eq 'not-nixos') { $global:LASTEXITCODE=37 }
  } elseif ($call -contains 'eth0') {
    if ($env:TEST_CASE -eq 'wsl-query-failure') { $global:LASTEXITCODE=29 }
    '[{"addr_info":[{"family":"inet","local":"172.30.0.9"}]}]'
  } else { throw 'Unexpected WSL operation' }
}
# No real HTTP calls: synthetic release bytes and a synthetic immutable SHA.
function Invoke-WebRequest {
  param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile, [int]$TimeoutSec)
  Record 'download' @{uri=$Uri; path=$OutFile}
  if ($env:TEST_CASE -eq 'download-failure') {
    [IO.File]::WriteAllText($OutFile, 'partial download')
    throw 'Mock network failure'
  }
  $body = if ($env:TEST_CASE -eq 'download-bad-hash') { 'corrupt rootfs' } else { 'synthetic rootfs, never imported' }
  [IO.File]::WriteAllText($OutFile, $body)
}
function Invoke-RestMethod {
  param([string]$Uri, [hashtable]$Headers, [int]$TimeoutSec)
  Record 'resolve' $Uri
  if ($env:TEST_CASE -eq 'api-failure') { throw 'Mock GitHub failure' }
  if ($env:TEST_CASE -eq 'api-empty') { return @() }
  if ($env:TEST_CASE -eq 'api-bad-sha') { return @([PSCustomObject]@{sha='main'}) }
  return @([PSCustomObject]@{sha=('b' * 40)})
}
function Get-FileHash {
  param([string]$LiteralPath, [string]$Algorithm)
  Record 'hash' $LiteralPath
  $hash = Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $LiteralPath -Algorithm $Algorithm
  # Exercise ALL default parameters without downloading the real 578 MB asset.
  if ($env:TEST_CASE -eq 'defaults') {
    if ([IO.File]::ReadAllText($LiteralPath) -cne 'synthetic rootfs, never imported') { throw 'Unexpected mock rootfs' }
    $hash.Hash = 'e7180ad555fdcb8e1e057e2ef056de467603a5e502ff8531053738371be3f6b9'
  }
  return $hash
}
function netsh.exe {
  $call = @($args); Record 'netsh' $call; $global:LASTEXITCODE=0
  if ($call -contains 'show') {
    if ($env:TEST_CASE -eq 'orphan') { '192.168.1.50 2222 172.30.0.8 22' }
    if ($env:TEST_CASE -eq 'wildcard') { '0.0.0.0 2222 172.30.0.8 22' }
    if ($env:TEST_CASE -in @('owned', 'remove')) { '192.168.1.50 2222 172.30.0.8 22' }
    if ($env:TEST_CASE -eq 'owned-mismatch') { '192.168.1.50 2222 172.30.0.99 22' }
  } elseif ($call -contains 'add' -and $env:TEST_CASE -eq 'add-failure') { $global:LASTEXITCODE=31 }
}
function Get-NetIPAddress {
  [CmdletBinding()] param([string]$AddressFamily, [string]$IPAddress)
  if ($IPAddress -eq '192.168.1.50') { [PSCustomObject]@{IPAddress=$IPAddress; InterfaceIndex=1} }
  elseif ($IPAddress -eq '172.30.0.9' -and $env:TEST_CASE -eq 'mirrored') { [PSCustomObject]@{IPAddress=$IPAddress} }
  elseif ($ErrorActionPreference -eq 'Stop') { throw 'Address not assigned to Windows' }
}
function Get-NetFirewallProfile {
  [PSCustomObject]@{Enabled=($env:TEST_CASE -ne 'firewall-disabled'); DefaultInboundAction='Block'}
}
function Get-NetTCPConnection {
  [CmdletBinding()] param([string]$State, [int]$LocalPort)
  if ($env:TEST_CASE -eq 'occupied') { [PSCustomObject]@{LocalAddress='192.168.1.50'} }
}
function Get-NetFirewallRule {
  [CmdletBinding()] param([string]$Name)
  if ($env:TEST_CASE -in @('owned','owned-mismatch','remove','rule-orphan')) { [PSCustomObject]@{Name=$Name} }
}
function Remove-NetFirewallRule {
  [CmdletBinding()] param([Parameter(ValueFromPipeline=$true)]$InputObject)
  process { if ($null -ne $InputObject) { Record 'remove-rule' $InputObject.Name } }
}
function New-NetFirewallRule {
  [CmdletBinding()] param([string]$Name,[string]$DisplayName,[string]$Direction,[string]$Action,[string]$Protocol,
    [string]$LocalAddress,[int]$LocalPort,[string[]]$RemoteAddress,[string]$Profile)
  Record 'new-rule' @{name=$Name; address=$LocalAddress; port=$LocalPort; remote=$RemoteAddress; profile=$Profile}
  if ($env:TEST_CASE -eq 'firewall-failure') { throw 'Mock rule failure' }
}
function icacls.exe { Record 'icacls' @($args); $global:LASTEXITCODE=0 }
function Start-Service { param([string]$Name) Record 'start-service' $Name }
function Test-NetConnection {
  param([string]$ComputerName,[int]$Port,[string]$InformationLevel)
  return $env:TEST_CASE -ne 'ssh-down'
}
$options = Get-Content -LiteralPath $env:TEST_OPTIONS -Raw | ConvertFrom-Json -AsHashtable
$positional = @($env:TEST_POSITIONAL | ConvertFrom-Json)
& $env:TEST_SCRIPT @positional @options
'''


class ScriptTests(unittest.TestCase):
    def test_guest_bash_and_static_boundaries(self):
        install = (ROOT / "scripts/install.ps1").read_text()
        network = (ROOT / "scripts/expose-ssh.ps1").read_text()
        guest = re.search(r"\$guest = @'\n(.*?)\n'@", install, re.S).group(1)
        subprocess.run(["bash", "-n"], input=guest, text=True, check=True)
        for text in (install, network):
            self.assertNotIn("--unregister", text)
            self.assertNotIn("--shutdown", text)
            self.assertNotIn("StrictHostKeyChecking=no", text)
            self.assertNotIn("AGE-SECRET-KEY-", text)
        self.assertIn("[a-f0-9]{40}", install)
        self.assertIn("Get-FileHash", install)
        self.assertIn("--no-write-lock-file", guest)
        self.assertNotIn("age-keygen", guest)
        self.assertIn("authorizedKeys", guest)
        self.assertIn("-RemoteAddress $AllowedRemoteAddress", network)
        self.assertIn("ownership record", network)
        self.assertIn("-Remove", (ROOT / "README.md").read_text())
        self.assertNotIn("portproxy reset", network)
        self.assertNotIn("DefaultInboundAction Allow", network)

    def test_public_source_and_selected_lock(self):
        lock = json.loads((ROOT / "flake.lock").read_text())
        self.assertEqual(set(lock["nodes"][lock["root"]]["inputs"]), {"nixpkgs", "nixos-wsl"})
        root = lock["nodes"][lock["root"]]["inputs"]
        self.assertEqual(set(lock["nodes"]), {"root", root["nixpkgs"], root["nixos-wsl"], "flake-compat"})
        for path in [ROOT / "flake.nix", ROOT / "host.nix", *ROOT.glob("modules/*.nix"), *ROOT.glob("scripts/*.ps1")]:
            text = path.read_text()
            for bait in ("AGE-SECRET-KEY-", "BEGIN OPENSSH PRIVATE KEY", "containers.yaml", "agents-api.yaml", "/home/swayf/projects", "personal.yaml"):
                self.assertNotIn(bait, text, str(path))
        key = (ROOT / "ssh-client.pub").read_text().strip()
        self.assertTrue(key.startswith("ssh-rsa "))
        result = subprocess.run(["ssh-keygen", "-lf", str(ROOT / "ssh-client.pub")], capture_output=True, text=True, check=True)
        self.assertIn("3072 SHA256:5X/MGOxI4MYB+q/B/L6ZOjzCRDfg36uBrljsZt+109c", result.stdout)

    def run_ps(self, name, case, options, success, defaults=False, positional=()):
        with tempfile.TemporaryDirectory(prefix="wsl-base-script-test-") as tmp:
            tmp = Path(tmp)
            original = ROOT / "scripts" / name
            script = tmp / name
            # Preserve original parsing; remove only the administrator directive in
            # the MOCK copy so Linux PowerShell can invoke it. All native/network
            # operations below are substituted with harness functions.
            script.write_text(original.read_text().replace("#Requires -RunAsAdministrator\n", ""))
            harness = tmp / "harness.ps1"
            harness.write_text(HARNESS)
            image = tmp / "fixture.wsl"
            image.write_bytes(b"synthetic rootfs, never imported")
            if name == "install.ps1":
                if case.startswith("download-"):
                    image.unlink()
                if case == "nonempty":
                    (tmp / "installation").mkdir()
                    (tmp / "installation/keep.txt").write_text("existing data")
                if case == "image-directory":
                    image.unlink()
                    image.mkdir()
                if case == "image-symlink":
                    image.rename(tmp / "real.wsl")
                    image.symlink_to(tmp / "real.wsl")
                if not defaults:
                    options = {"Flake": "github:fixture/wsl-base/" + "a" * 40 + "#wsl-base",
                               "ImagePath": str(image), "ImageSha256": hashlib.sha256(b"synthetic rootfs, never imported").hexdigest(),
                               "InstallDirectory": str(tmp / "installation")} | options
            else:
                options = {"ListenAddress": "192.168.1.50", "AllowedRemoteAddress": ["192.168.1.10", "100.64.0.10/32"]} | options
                if case in ("owned", "owned-mismatch", "remove"):
                    state = tmp / "nixos-wsl-base"
                    state.mkdir()
                    (state / "NixOS-WSL-NixOS-Managed-192-168-1-50-2222.json").write_text(json.dumps({
                        "owner": "NixOS-WSL-NixOS-Managed-192-168-1-50-2222", "distribution": "NixOS-Managed",
                        "listenAddress": "192.168.1.50", "listenPort": 2222, "targetAddress": "172.30.0.8", "targetPort": 22}))
            options_file = tmp / "options.json"
            options_file.write_text(json.dumps(options))
            log = tmp / "calls.jsonl"
            env = os.environ | {"TEST_SCRIPT": str(script), "TEST_ORIGINAL": str(original), "TEST_CASE": case,
                                "TEST_LOG": str(log), "TEST_OPTIONS": str(options_file),
                                "TEST_POSITIONAL": json.dumps(positional),
                                "LOCALAPPDATA": str(tmp), "ProgramData": str(tmp)}
            result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, success, case + "\n" + result.stdout + result.stderr)
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            state = tmp / "nixos-wsl-base/NixOS-WSL-NixOS-Managed-192-168-1-50-2222.json"
            record = json.loads(state.read_text(encoding="utf-8-sig")) if state.exists() else None
            if name == "install.ps1":
                self.assertEqual(list(tmp.glob(".nixos-download-*.wsl")), [], "Temporary download must be cleaned up")
                if case.startswith("download-"):
                    self.assertEqual(image.exists(), success, "Only verified downloads may reach the final path")
                if case == "bad-hash":
                    self.assertEqual(image.read_bytes(), b"synthetic rootfs, never imported")
                if case == "defaults":
                    self.assertEqual((tmp / "nixos.wsl").read_bytes(), b"synthetic rootfs, never imported")
            return calls, record

    @unittest.skipUnless(PWSH, "pwsh unavailable")
    def test_install_native_mocks(self):
        calls, _ = self.run_ps("install.ps1", "new", {"RestartDistribution": True}, True)
        operations = [c["value"][0] for c in calls if c["kind"] == "wsl"]
        self.assertEqual(operations, ["--list", "--import", "--distribution", "--terminate"])
        self.assertEqual(next(c["value"] for c in calls if c["kind"] == "wsl" and c["value"][0] == "--import")[1], "NixOS-Managed")
        self.assertFalse(any(c["kind"] in ("download", "resolve") for c in calls))
        for case, options in (("existing", {}), ("list-failure", {}), ("import-failure", {}),
                              ("guest-failure", {}), ("bad-hash", {"ImageSha256": "0" * 64}),
                              ("nonempty", {}), ("image-directory", {}), ("image-symlink", {}),
                              ("moving-ref", {"Flake": "github:fixture/wsl-base/main#wsl-base"})):
            with self.subTest(case=case):
                calls, _ = self.run_ps("install.ps1", case, options, False)
                self.assertFalse(any(c["value"][0] == "--terminate" for c in calls if c["kind"] == "wsl"))
                if case not in ("import-failure", "guest-failure"):
                    self.assertFalse(any(c["value"][0] == "--import" for c in calls if c["kind"] == "wsl"))
                self.assertFalse(any(c["kind"] in ("download", "resolve") for c in calls))

    @unittest.skipUnless(PWSH, "pwsh unavailable")
    def test_install_distribution_binding(self):
        for positional, named in ((("NixOS-Work",), {}), ((), {"Distribution": "NixOS-Work"})):
            with self.subTest(positional=positional):
                calls, _ = self.run_ps("install.ps1", "new", named | {
                    "RestartDistribution": True, "InstallDirectory": None,
                }, True, positional=positional)
                wsl = [c["value"] for c in calls if c["kind"] == "wsl"]
                imported = next(c for c in wsl if c[0] == "--import")
                self.assertEqual(imported[1], "NixOS-Work")
                self.assertEqual(Path(imported[2]).name, "NixOS-Work")
                guest = next(c for c in wsl if "/bin/bash" in c)
                self.assertEqual(guest[1], "NixOS-Work")
                self.assertEqual(guest[-1], "github:fixture/wsl-base/" + "a" * 40 + "#wsl-base")
                self.assertEqual(wsl[-1], ["--terminate", "NixOS-Work"])
        for positional in (("invalid_name",), ("NixOS-Work", "unexpected")):
            with self.subTest(positional=positional):
                calls, _ = self.run_ps("install.ps1", "new", {}, False, positional=positional)
                self.assertEqual(calls, [], "Binding errors must occur before any operation")

    @unittest.skipUnless(PWSH, "pwsh unavailable")
    def test_install_defaults_and_download_mocks(self):
        calls, _ = self.run_ps("install.ps1", "defaults", {}, True, defaults=True)
        download = next(c["value"] for c in calls if c["kind"] == "download")
        self.assertEqual(download["uri"], "https://github.com/nix-community/NixOS-WSL/releases/download/2605.7.2/nixos.wsl")
        self.assertEqual([c["value"] for c in calls if c["kind"] == "resolve"],
                         ["https://api.github.com/repos/swayf/nixos-wsl-base/commits?per_page=1"])
        guest = next(c["value"] for c in calls if c["kind"] == "wsl" and "/bin/bash" in c["value"])
        self.assertEqual(guest[-1], "github:swayf/nixos-wsl-base/" + "b" * 40 + "#wsl-base")
        for case in ("download-new", "download-failure", "download-bad-hash"):
            success = case == "download-new"
            calls, _ = self.run_ps("install.ps1", case, {}, success)
            self.assertEqual(sum(c["kind"] == "download" for c in calls), 1)
            self.assertEqual(any(c["kind"] == "wsl" and c["value"][0] == "--import" for c in calls), success)
            self.assertFalse(any(c["kind"] == "resolve" for c in calls))
        for flake in ("github:fixture/wsl-base", "github:fixture/wsl-base#other-host",
                      "github:fixture/wsl-base/" + "a" * 40):
            calls, _ = self.run_ps("install.ps1", "resolve", {"Flake": flake}, True)
            guest = next(c["value"] for c in calls if c["kind"] == "wsl" and "/bin/bash" in c["value"])
            host = "other-host" if "#other-host" in flake else "wsl-base"
            sha = "a" * 40 if "/" + "a" * 40 in flake else "b" * 40
            self.assertEqual(guest[-1], f"github:fixture/wsl-base/{sha}#{host}")
            self.assertEqual(sum(c["kind"] == "resolve" for c in calls), int(sha == "b" * 40))
            self.assertFalse(any(c["kind"] == "download" for c in calls))
        for case in ("api-failure", "api-empty", "api-bad-sha"):
            calls, _ = self.run_ps("install.ps1", case, {"Flake": "github:fixture/wsl-base"}, False)
            self.assertFalse(any(c["kind"] == "wsl" and c["value"][0] == "--import" for c in calls))

    @unittest.skipUnless(PWSH, "pwsh unavailable")
    def test_nat_exposure_native_mocks(self):
        for case in ("new", "owned"):
            calls, record = self.run_ps("expose-ssh.ps1", case, {}, True)
            rule = next(c["value"] for c in calls if c["kind"] == "new-rule")
            self.assertEqual(rule["address"], "192.168.1.50")
            self.assertEqual(rule["remote"], ["192.168.1.10", "100.64.0.10/32"])
            self.assertEqual(record["targetAddress"], "172.30.0.9")
            acl = next(c["value"] for c in calls if c["kind"] == "icacls")
            self.assertIn('*S-1-5-32-544:(OI)(CI)F', acl)
            self.assertIn('/inheritance:r', acl)
        calls, record = self.run_ps("expose-ssh.ps1", "dry-run", {"WhatIf": True}, True)
        self.assertIsNone(record)
        self.assertFalse(any(c["kind"] == "new-rule" or c["kind"] == "start-service" for c in calls))
        calls, record = self.run_ps("expose-ssh.ps1", "remove", {"Remove": True, "AllowedRemoteAddress": []}, True)
        self.assertIsNone(record)
        self.assertTrue(any(c["kind"] == "netsh" and "delete" in c["value"] for c in calls))
        for case, options in (("wildcard-ip", {"ListenAddress": "0.0.0.0"}),
                              ("no-clients", {"AllowedRemoteAddress": []}),
                              ("any-clients", {"AllowedRemoteAddress": ["Any"]}),
                              ("all-clients", {"AllowedRemoteAddress": ["10.0.0.0/0"]}),
                              ("orphan", {}), ("rule-orphan", {}), ("wildcard", {}),
                              ("owned-mismatch", {}), ("occupied", {}), ("firewall-disabled", {}),
                              ("ssh-down", {}), ("mirrored", {}), ("wsl-query-failure", {}), ("not-nixos", {})):
            with self.subTest(case=case):
                calls, _ = self.run_ps("expose-ssh.ps1", case, options, False)
                self.assertFalse(any(c["kind"] == "new-rule" or c["kind"] == "start-service" for c in calls))
                self.assertFalse(any(c["kind"] == "netsh" and any(x in c["value"] for x in ("add", "delete")) for c in calls))
        calls, _ = self.run_ps("expose-ssh.ps1", "firewall-failure", {}, False)
        self.assertTrue(any(c["kind"] == "netsh" and "delete" in c["value"] for c in calls))
        self.run_ps("expose-ssh.ps1", "add-failure", {}, False)


if __name__ == "__main__":
    unittest.main()
