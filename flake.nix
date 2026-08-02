{
  description = "ret2console — Ret2Shell (回归终端) CTF 比赛平台的终端用户接口";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f:
        nixpkgs.lib.genAttrs systems (system: f (import nixpkgs { inherit system; }));

      ret2console = pkgs:
        pkgs.python3.pkgs.buildPythonPackage {
          pname = "ret2console";
          version = "0.1.0";
          pyproject = true;
          src = ./.;
          build-system = [ pkgs.python3.pkgs.hatchling ];
          dependencies = with pkgs.python3.pkgs; [
            prompt-toolkit
            rich
            httpx
            pillow
          ];
          # 测试在 devShell 里用 uv run pytest 跑（本机另有完整测试套件）
          doCheck = false;
          meta = with pkgs.lib; {
            description = "Ret2Shell 比赛平台的终端接口（ret2console / r2c）";
            mainProgram = "ret2console";
            license = licenses.gpl3Only;
          };
        };

      mkApp = program: { type = "app"; program = "${program}"; };
    in
    {
      packages = forAllSystems (pkgs: {
        default = ret2console pkgs;
        ret2console = ret2console pkgs;
      });

      apps = forAllSystems (pkgs: {
        default = mkApp "${ret2console pkgs}/bin/ret2console";
        ret2console = mkApp "${ret2console pkgs}/bin/ret2console";
        r2c = mkApp "${ret2console pkgs}/bin/r2c";
      });

    };
}
