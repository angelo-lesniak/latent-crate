#!/usr/bin/env bash
set -Eeuo pipefail

# Resolves saved requirements from managed and local third-party nodes against
# the pinned ComfyUI environment, then builds only the missing packages.

requirements_root=${1:-/tmp/custom-node-requirements}
wheelhouse=${2:-/opt/latentcrate-node-wheelhouse}
core_constraints=/etc/latentcrate/comfy-environment-constraints.txt
node_constraints=/etc/latentcrate/custom-node-constraints.txt
manifest="$requirements_root/manifest.txt"

die() {
  printf 'LatentCrate: %s\n' "$*" >&2
  exit 1
}

[[ -s "$core_constraints" ]] || die "core Python constraints are missing: $core_constraints"
[[ -f "$node_constraints" ]] || die "third-party node constraints are missing: $node_constraints"
[[ "$wheelhouse" == /opt/latentcrate-node-wheelhouse ]] \
  || die "refusing unexpected third-party node package directory: $wheelhouse"

requirements=()
if [[ -f "$manifest" ]]; then
  while IFS= read -r relative || [[ -n "$relative" ]]; do
    [[ -n "$relative" ]] || continue
    if [[ "$relative" == /* || "$relative" =~ (^|/)\.\.(/|$) ]]; then
      die "unsafe saved requirement path: $relative"
    fi
    requirement="$requirements_root/$relative"
    [[ -f "$requirement" ]] \
      || die "saved requirement is missing: $relative"
    requirements+=("$requirement")
  done < "$manifest"
fi

rm -rf -- "$wheelhouse"
mkdir -p "$wheelhouse"

(
  cd "$requirements_root"
  LC_ALL=C find . -type f ! -name .gitkeep -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 -r sha256sum
) > "$wheelhouse/requirements.sha256"

# install-node-deps.sh hashes this record into the runtime environment
# manifest, so every produced wheelhouse must carry it, including the
# empty-requirements case below. The identity inputs arrive as build
# arguments when this script runs inside the node-deps-builder image stage.
{
  printf 'pytorch-devel-image=%s\n' "${PYTORCH_DEVEL_IMAGE:-}"
  printf 'custom-node-cache-key=%s\n' "${CUSTOM_NODE_CACHE_KEY:-}"
  printf 'cuda-architecture-list=%s\n' "${CUSTOM_NODE_CUDA_ARCH_LIST:-}"
  python --version
  python -c 'import torch; print(f"torch={torch.__version__}"); print(f"torch-cuda={torch.version.cuda}")'
  nvcc --version
} > "$wheelhouse/build-environment.txt"

if ((${#requirements[@]} == 0)); then
  : > "$wheelhouse/wheels.sha256"
  : > "$wheelhouse/install-requirements.txt"
  : > "$wheelhouse/base-satisfied-requirements.txt"
  : > "$wheelhouse/resolved-missing-requirements.txt"
  printf '{"install": []}\n' > "$wheelhouse/resolution-report.json"
  printf 'LatentCrate: no saved third-party node requirements were supplied.\n'
  exit 0
fi

printf 'LatentCrate: building CUDA-compatible Python packages from %d requirement file(s):\n' \
  "${#requirements[@]}"
printf '  %s\n' "${requirements[@]}"

resolution_report="$wheelhouse/resolution-report.json"
resolution_requirements="$wheelhouse/resolved-missing-requirements.txt"
resolve_args=(
  --dry-run
  --report "$resolution_report"
  --constraint "$core_constraints"
  --constraint "$node_constraints"
  --prefer-binary
  --no-build-isolation
)
for requirement in "${requirements[@]}"; do
  resolve_args+=(--requirement "$requirement")
done

# Build isolation is disabled only in this dedicated CUDA development stage so
# packages whose build backend imports Torch can see the pinned Torch/CUDA ABI.
# The final image does not contain a compiler. It installs from this package
# directory with networking disabled.
python -m pip install "${resolve_args[@]}"
python /usr/local/lib/latentcrate/create-node-resolution.py \
  "$resolution_report" "$resolution_requirements"

if [[ -s "$resolution_requirements" ]]; then
  python -m pip wheel \
    --wheel-dir "$wheelhouse" \
    --constraint "$core_constraints" \
    --constraint "$node_constraints" \
    --prefer-binary \
    --no-build-isolation \
    --no-deps \
    --requirement "$resolution_requirements"
fi

while IFS= read -r -d '' wheel; do
  wheel_name=${wheel##*/}
  [[ "$wheel_name" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*\.whl$ ]] \
    || die "unsafe wheel filename produced by third-party node build: $wheel_name"
done < <(find "$wheelhouse" -maxdepth 1 -type f -name '*.whl' -print0)
if [[ -n "$(find "$wheelhouse" -maxdepth 1 -type l -print -quit)" ]]; then
  die 'third-party node builds must not publish symbolic links in the wheel directory'
fi

python /usr/local/lib/latentcrate/create-node-package-lock.py \
  "$wheelhouse" "$wheelhouse/install-requirements.txt" \
  --base-constraints /etc/latentcrate/comfy-environment-constraints.txt \
  --base-satisfied-output "$wheelhouse/base-satisfied-requirements.txt"

(
  cd "$wheelhouse"
  LC_ALL=C find . -maxdepth 1 -type f -name '*.whl' -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 -r sha256sum
) > "$wheelhouse/wheels.sha256"
