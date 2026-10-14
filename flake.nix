{
  description = "Minimal SSH-managed NixOS-WSL base; no application credentials";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/151fa4e8ddfdd8dd25d945ad94ed54a13de9f6e4";
  inputs.nixos-wsl.url = "github:nix-community/NixOS-WSL/72c92b11bb8289e6651c7fef29cc0a885fd6a255";
  inputs.nixos-wsl.inputs.nixpkgs.follows = "nixpkgs";

  outputs = inputs@{ self, nixpkgs, nixos-wsl }: {
    # Generic mechanism: the caller imports NixOS-WSL and supplies its own keys.
    nixosModules.management = ./modules/management.nix;
    # This operator's public base: include it in EVERY remotely deployed profile.
    nixosModules.default = {
      imports = [ nixos-wsl.nixosModules.default self.nixosModules.management ./host.nix ];
    };
    nixosConfigurations.wsl-base = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [ self.nixosModules.default ];
    };
    checks.x86_64-linux = import ./tests/eval.nix { inherit inputs; };
  };
}
