# Public operator declaration, not a fleet inventory or an identity file.
{ lib, ... }: {
  wsl.defaultUser = "swayf";
  networking.hostName = lib.mkDefault "wsl-managed";
  system.stateVersion = "26.05";
  wslManagement.authorizedKeys = [ (lib.removeSuffix "\n" (builtins.readFile ./ssh-client.pub)) ];
}
