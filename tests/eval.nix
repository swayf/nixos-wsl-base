{ inputs }:
let
  inherit (inputs) self nixpkgs nixos-wsl;
  pkgs = nixpkgs.legacyPackages.x86_64-linux;
  lib = nixpkgs.lib;
  base = self.nixosConfigurations.wsl-base.config;
  addon = (nixpkgs.lib.nixosSystem {
    system = "x86_64-linux";
    modules = [ self.nixosModules.default ({ pkgs, ... }: {
      networking.hostName = "wsl-apps";
      environment.systemPackages = [ pkgs.jq ];
    }) ];
  }).config;
  caller = keys: (nixpkgs.lib.nixosSystem {
    system = "x86_64-linux";
    modules = [ nixos-wsl.nixosModules.default self.nixosModules.management {
      wsl.defaultUser = "alice";
      networking.hostName = "fixture-wsl";
      system.stateVersion = "26.05";
      wslManagement.authorizedKeys = keys;
      wslManagement.sshPort = 2222;
    } ];
  }).config;
  alice = caller [ (lib.removeSuffix "\n" (builtins.readFile ../ssh-client.pub)) ];
  missing = caller [ ];
  check = c: user:
    c.wsl.enable && c.wsl.wslConf.boot.systemd
    && c.services.openssh.enable && c.networking.firewall.enable
    && c.services.openssh.settings.PasswordAuthentication == false
    && c.services.openssh.settings.KbdInteractiveAuthentication == false
    && c.services.openssh.settings.PermitRootLogin == "no"
    && c.services.openssh.settings.AllowUsers == [ user ]
    && c.services.openssh.settings.AllowAgentForwarding == false
    && c.services.openssh.settings.AllowTcpForwarding == false
    && c.users.users.${user}.openssh.authorizedKeys.keys != [ ]
    && lib.elem user c.nix.settings.trusted-users
    && c.security.sudo.wheelNeedsPassword
    && lib.any (rule: rule.users == [ user ] && lib.any (cmd: cmd.options == [ "NOPASSWD" ]) rule.commands) c.security.sudo.extraRules;
  failures = c: builtins.filter (a: !a.assertion) c.assertions;
  valid = assert check base "swayf";
    assert check addon "swayf";
    assert check alice "alice";
    assert base.services.openssh.ports == [ 22 ] && alice.services.openssh.ports == [ 2222 ];
    assert base.users.users.swayf.openssh.authorizedKeys.keys == addon.users.users.swayf.openssh.authorizedKeys.keys;
    assert failures base == [ ] && failures addon == [ ] && failures alice == [ ];
    assert lib.any (a: a.message == "SSH management requires at least one public client key.") (failures missing);
    assert !(base ? home-manager) && !(base ? sops) && !base.virtualisation.podman.enable;
    "validated";
in {
  management = pkgs.runCommand "wsl-management-contract" { nativeBuildInputs = [ pkgs.openssh ]; } ''
    test ${lib.escapeShellArg valid} = validated
    ssh-keygen -lf ${../ssh-client.pub} > /dev/null
    touch "$out"
  '';
}
