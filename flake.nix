{
  description = "A digest of nixpkgs' packages' upstream versions, for nixkeeper.";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # nixkeeper's code for GitHub releases (releases.py): how it reads a
    # package's source from nixpkgs, works out its tag scheme, orders
    # versions and asks GitHub, so the digest says what nixkeeper would.
    nixkeeper = {
      url = "github:nixkeeper/nixkeeper";
      inputs = {
        nixpkgs.follows = "nixpkgs";
        flake-utils.follows = "flake-utils";
        treefmt-nix.follows = "treefmt-nix";
        nix-darwin.inputs.nixpkgs.follows = "nixpkgs";
      };
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      treefmt-nix,
      nixkeeper,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
        inherit (pkgs) lib;

        # The code and its tests, run with nixkeeper's source beside them (for
        # GitHub releases), with the Python packages nixkeeper's modules
        # import. The rest needs the standard library only.
        src = lib.fileset.toSource {
          root = ./.;
          fileset = lib.fileset.unions [
            ./nixkeeper_versions
            ./tests
          ];
        };
        python = pkgs.python3.withPackages (ps: [ ps.brotli ]);
        pythonPath = "${src}:${nixkeeper}";

        # `nix fmt` formats everything; checks.formatting fails on anything
        # unformatted. ruff's settings live in pyproject.toml.
        treefmt = treefmt-nix.lib.evalModule pkgs {
          projectRootFile = "flake.nix";
          programs = {
            nixfmt.enable = true;
            ruff-format.enable = true;
            ruff-check.enable = true; # safe auto-fixes, e.g. import order
          };
        };

        linters = with pkgs; [
          ruff
          deadnix
          statix
          actionlint
          shellcheck # used by actionlint for the workflows' run: scripts
        ];
      in
      {
        formatter = treefmt.config.build.wrapper;

        # `nix run`: brings the digest in ./data (or the folder given) up to
        # date, as the workflow does.
        apps.default = {
          type = "app";
          program = lib.getExe (
            pkgs.writeShellScriptBin "nixkeeper-versions" ''
              PYTHONPATH=${pythonPath} exec ${lib.getExe python} -m nixkeeper_versions "$@"
            ''
          );
          meta.description = "Bring the digest of nixpkgs projects' versions up to date";
        };

        checks = {
          tests =
            pkgs.runCommand "nixkeeper-versions-tests"
              {
                nativeBuildInputs = [ python ];
                # The deadline tests run a server on 127.0.0.1, which macOS's
                # sandbox blocks unless asked.
                __darwinAllowLocalNetworking = true;
              }
              ''
                cd ${src}
                PYTHONPATH=${pythonPath} python3 -m unittest discover -s tests -t . -v
                touch $out
              '';
          formatting = treefmt.config.build.check self;
          lint = pkgs.runCommand "nixkeeper-versions-lint" { nativeBuildInputs = linters; } ''
            cd ${self}
            export HOME=$TMPDIR
            ruff check --no-cache .
            deadnix --fail .
            statix check .
            # Named explicitly: on its own actionlint looks for .git, which the
            # flake source (CI's view of the repo) doesn't include.
            actionlint .github/workflows/*.yml
            shellcheck scripts/*.sh
            touch $out
          '';
        };

        devShells.default = pkgs.mkShell {
          packages = [
            python
            treefmt.config.build.wrapper
          ]
          ++ linters;
          # nixkeeper's source, as the app and the tests have it.
          shellHook = "export PYTHONPATH=${nixkeeper}\${PYTHONPATH:+:$PYTHONPATH}";
        };
      }
    );
}
