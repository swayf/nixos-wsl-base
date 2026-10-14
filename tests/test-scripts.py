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
& $env:TEST_SCRIPT @options
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

    def run_ps(self, name, case, options, success):
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
                options = {"Flake": "github:fixture/wsl-base/" + "a" * 40 + "#wsl-base",
                           "ImagePath": str(image), "ImageSha256": hashlib.sha256(image.read_bytes()).hexdigest(),
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
                                "LOCALAPPDATA": str(tmp), "ProgramData": str(tmp)}
            result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, success, case + "\n" + result.stdout + result.stderr)
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            state = tmp / "nixos-wsl-base/NixOS-WSL-NixOS-Managed-192-168-1-50-2222.json"
            record = json.loads(state.read_text(encoding="utf-8-sig")) if state.exists() else None
            return calls, record

    @unittest.skipUnless(PWSH, "pwsh unavailable")
    def test_install_native_mocks(self):
        calls, _ = self.run_ps("install.ps1", "new", {"RestartDistribution": True}, True)
        operations = [c["value"][0] for c in calls if c["kind"] == "wsl"]
        self.assertEqual(operations, ["--list", "--import", "--distribution", "--terminate"])
        self.assertEqual(calls[1]["value"][1], "NixOS-Managed")
        for case, options in (("existing", {}), ("list-failure", {}), ("import-failure", {}),
                              ("guest-failure", {}), ("bad-hash", {"ImageSha256": "0" * 64}),
                              ("moving-ref", {"Flake": "github:fixture/wsl-base/main#wsl-base"})):
            with self.subTest(case=case):
                calls, _ = self.run_ps("install.ps1", case, options, False)
                self.assertFalse(any(c["value"][0] == "--terminate" for c in calls if c["kind"] == "wsl"))
                if case in ("existing", "list-failure", "bad-hash", "moving-ref"):
                    self.assertFalse(any(c["value"][0] == "--import" for c in calls if c["kind"] == "wsl"))

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
