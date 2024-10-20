{ pkgs ? import <nixpkgs> {} }:

let
  inherit (pkgs) python3Packages fetchPypi;
  pycord = python3Packages.buildPythonPackage rec {
    pname = "py-cord";
    version = "2.5.0";
    src = fetchPypi {
      inherit pname version;
      hash = "sha256-+vCK9dperC7T0cikPYMH1aHj8BYC3vKDMwydLN4LEWI=";
    };
    propagatedBuildInputs = [ python3Packages.aiohttp ];
    doCheck = false;
  };
  python3Env = pkgs.python3.withPackages (ps: with ps; [ attrs pycord python-dotenv ]);
in
  pkgs.mkShell {
    buildInputs = [ 
      python3Env
    ];
}

