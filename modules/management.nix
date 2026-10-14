# Management is shared by base and application profiles; no HM/SOPS inputs.
{ config, lib, pkgs, ... }:
let
  cfg = config.wslManagement;
  user = config.wsl.defaultUser;
  publicKey = "(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com)[ \t]+[A-Za-z0-9+/=]+([ \t]+[^\r\n]*)?";
in {
  options.wslManagement = {
    authorizedKeys = lib.mkOption {
      type = lib.types.listOf (lib.types.strMatching publicKey);
      default = [ ];
      description = "Operator PUBLIC SSH keys, without authorized_keys options; never private keys.";
    };
    sshPort = lib.mkOption {
      type = lib.types.port;
      default = 22;
      description = "Guest SSH port; Windows NAT exposure is configured separately.";
    };
  };
  config = {
    assertions = [
      { assertion = cfg.authorizedKeys != [ ]; message = "SSH management requires at least one public client key."; }
      { assertion = builtins.match "[a-z_][a-z0-9_-]{0,31}" user != null && !lib.elem user [ "root" "nobody" ];
        message = "SSH deployment requires an ordinary WSL user."; }
    ];
    wsl.enable = true;
    wsl.wslConf.boot.systemd = true;
    nix.settings = {
      experimental-features = [ "nix-command" "flakes" ];
      # Remote nix copy needs to import this administrator's locally built closure.
      # Both this trust and passwordless deployment sudo are root-equivalent.
      trusted-users = [ "root" user ];
    };
    users.users.${user} = {
      extraGroups = [ "wheel" ];
      linger = true;
      openssh.authorizedKeys.keys = cfg.authorizedKeys;
    };
    # Do not grant future unrelated wheel users the upstream blanket exception.
    security.sudo.wheelNeedsPassword = lib.mkForce true;
    security.sudo.extraRules = [{
      users = [ user ];
      commands = [{ command = "ALL"; options = [ "NOPASSWD" ]; }];
    }];
    services.openssh = {
      enable = true;
      openFirewall = true;
      ports = [ cfg.sshPort ];
      settings = {
        AddressFamily = "inet";
        PubkeyAuthentication = true;
        PasswordAuthentication = false;
        KbdInteractiveAuthentication = false;
        PermitRootLogin = "no";
        AllowUsers = [ user ];
        AllowAgentForwarding = false;
        AllowTcpForwarding = false;
        AllowStreamLocalForwarding = false;
        X11Forwarding = false;
      };
    };
    # Explicit guest firewall; never change Windows firewall from Nix activation.
    networking.firewall.enable = true;
    environment.systemPackages = [ pkgs.git pkgs.openssh pkgs.age pkgs.sops ];
    systemd.tmpfiles.rules = [ "d /home/${user}/.config/sops/age 0700 ${user} users -" ];
  };
}
