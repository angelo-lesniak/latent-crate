#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
readonly PROJECT_ROOT

if (($# != 6)); then
  printf 'usage: custom-node-cache-key.sh <devel-image> <runtime-image> <comfy-ref> <torchcodec-version> <torchcodec-index> <cuda-architectures>\n' >&2
  exit 2
fi

command -v sha256sum >/dev/null 2>&1 \
  || { printf 'LatentCrate: sha256sum is required to identify the third-party node dependency cache\n' >&2; exit 1; }

(
  cd "$PROJECT_ROOT"
  printf '%s\0' \
    "PYTORCH_DEVEL_IMAGE=$1" \
    "PYTORCH_RUNTIME_IMAGE=$2" \
    "COMFYUI_REF=$3" \
    "TORCHCODEC_VERSION=$4" \
    "TORCHCODEC_INDEX_URL=$5" \
    "CUSTOM_NODE_CUDA_ARCH_LIST=$6"
  # IMPORTANT: this file list must stay in sync with the COPY inputs of the
  # node-deps build stage in services/comfy/Dockerfile (the block that copies
  # build-node-deps.sh, create-node-package-lock.py, create-node-resolution.py,
  # and the constraint files, around lines 343-353). If a file is added to or
  # removed from that stage's inputs, update this list too; otherwise a stale
  # pip cache keyed on the old hash will be reused for changed builds.
  sha256sum \
    services/comfy/Dockerfile \
    services/comfy/build-node-deps.sh \
    services/comfy/create-node-package-lock.py \
    services/comfy/create-node-resolution.py \
    services/comfy/install-node-deps.sh \
    config/python/custom-node-constraints.txt \
    config/python/runtime-constraints.txt
) | sha256sum | cut -c 1-32
