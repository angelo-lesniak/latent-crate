#!/usr/bin/env bash
set -Eeuo pipefail

# Installs locked Python dependencies from managed and local third-party nodes
# into an isolated package tree. The Dockerfile runs this without network access
# and as an unprivileged user.

wheelhouse=${1:-/tmp/latentcrate-node-wheelhouse}
target=${2:-/opt/latentcrate-node-site}
record=${3:-/opt/latentcrate-node-record}
core_constraints=/etc/latentcrate/comfy-environment-constraints.txt
node_constraints=/etc/latentcrate/custom-node-constraints.txt
wheel_manifest="$wheelhouse/wheels.sha256"
package_lock="$wheelhouse/install-requirements.txt"
base_satisfied="$wheelhouse/base-satisfied-requirements.txt"

if [[ ! -s "$core_constraints" ]]; then
  printf 'LatentCrate: core Python constraints are missing: %s\n' "$core_constraints" >&2
  exit 1
fi
if [[ ! -f "$node_constraints" ]]; then
  printf 'LatentCrate: third-party node constraints are missing: %s\n' "$node_constraints" >&2
  exit 1
fi
if [[ ! -f "$wheel_manifest" || ! -f "$package_lock" || ! -f "$base_satisfied" ]]; then
  printf 'LatentCrate: third-party node package manifests are missing from %s\n' "$wheelhouse" >&2
  exit 1
fi
[[ "$target" == /opt/latentcrate-node-site ]] \
  || { printf 'LatentCrate: refusing unexpected third-party node target: %s\n' "$target" >&2; exit 1; }
[[ "$record" == /opt/latentcrate-node-record ]] \
  || { printf 'LatentCrate: refusing unexpected third-party node record: %s\n' "$record" >&2; exit 1; }
[[ -d "$target" && -w "$target" && -d "$record" && -w "$record" ]] \
  || { printf 'LatentCrate: third-party node target and record must be writable directories\n' >&2; exit 1; }

if [[ -s "$wheel_manifest" ]]; then
  if [[ -n "$(find "$wheelhouse" -maxdepth 1 -type l -print -quit)" ]]; then
    printf 'LatentCrate: third-party node package directory contains a symbolic link\n' >&2
    exit 1
  fi
  (
    cd "$wheelhouse"
    sha256sum --check --strict wheels.sha256
  )
fi

if [[ ! -s "$package_lock" ]]; then
  printf 'LatentCrate: no additional third-party node packages are needed outside the base environment.\n'
else
  printf 'LatentCrate: installing locally built third-party node packages:\n'
  sed 's/^/  /' "$package_lock"

  python -m pip install \
    --constraint "$core_constraints" \
    --constraint "$node_constraints" \
    --no-deps \
    --no-index \
    --only-binary=:all: \
    --find-links "$wheelhouse" \
    --target "$target" \
    --requirement "$package_lock"
fi

if [[ -n "$(find "$target" -type l -print -quit)" ]]; then
  printf 'LatentCrate: third-party node packages must not install symbolic links\n' >&2
  exit 1
fi
if [[ -n "$(find "$target" ! -type d ! -type f -print -quit)" ]]; then
  printf 'LatentCrate: third-party node packages installed an unsupported file type\n' >&2
  exit 1
fi

# PYTHONPATH makes the isolated package directory importable without treating it
# as a site directory. Wheel-provided .pth files therefore are not processed.
PYTHONPATH="$target${PYTHONPATH:+:$PYTHONPATH}" python -m pip check

{
  printf 'base-environment='
  cat /usr/local/share/latentcrate/base-python-environment.id
  printf 'requirements-manifest='
  sha256sum "$wheelhouse/requirements.sha256" | cut -d ' ' -f 1
  printf 'wheel-manifest='
  sha256sum "$wheel_manifest" | cut -d ' ' -f 1
  printf 'package-lock='
  sha256sum "$package_lock" | cut -d ' ' -f 1
  printf 'base-satisfied-lock='
  sha256sum "$base_satisfied" | cut -d ' ' -f 1
  printf 'resolution-report='
  sha256sum "$wheelhouse/resolution-report.json" | cut -d ' ' -f 1
  printf 'build-environment='
  sha256sum "$wheelhouse/build-environment.txt" | cut -d ' ' -f 1
} > "$record/python-environment.manifest"
sha256sum "$record/python-environment.manifest" \
  | cut -d ' ' -f 1 > "$record/python-environment.id"
