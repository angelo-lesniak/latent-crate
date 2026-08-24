#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
readonly SOURCE_PROJECT_ROOT
TEST_ROOT=$(mktemp -d)
readonly TEST_ROOT
cleanup() {
  rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT

PROJECT_ROOT=$TEST_ROOT
mkdir -p "$PROJECT_ROOT/versions"
# shellcheck source=lib/latentcrate/core.sh
source "$SOURCE_PROJECT_ROOT/lib/latentcrate/core.sh"
# shellcheck source=lib/latentcrate/versions.sh
source "$SOURCE_PROJECT_ROOT/lib/latentcrate/versions.sh"

fake_bin="$TEST_ROOT/fake-bin"
mkdir -p "$fake_bin"
cp "$SOURCE_PROJECT_ROOT/tests/fixtures/fake-bin/docker" "$fake_bin/docker"
chmod +x "$fake_bin/docker"
export PATH="$fake_bin:$PATH"
export CONTAINER_ENGINE=docker

write_profile() {
  printf '%s\n' \
    'PYTORCH_DEVEL_IMAGE=docker.io/pytorch/pytorch:2.8.0-cuda13.0-cudnn9-devel' \
    'PYTORCH_RUNTIME_IMAGE=docker.io/pytorch/pytorch:2.8.0-cuda13.0-cudnn9-runtime' \
    'COMFYUI_REF=v1.0.0' \
    'COMFYUI_FRONTEND_REF=Comfy-Org/ComfyUI_frontend@v1.2.0' \
    'COMFY_FRONTEND_DIST_SHA256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
    'FFMPEG_REF=n7.0.0' \
    'NV_CODEC_HEADERS_REF=n12.0.0' \
    'SVT_AV1_REF=v2.0.0' \
    'SAGEATTENTION_REF=v2.0.0' \
    'TORCHCODEC_VERSION=0.8.0+cu130' \
    'TORCHCODEC_INDEX_URL=https://download.pytorch.org/whl/cu130' \
    'FRONTEND_NODE_IMAGE=docker.io/library/node:24.0.0-bookworm-slim' \
    'FRONTEND_PNPM_VERSION=10.0.0' \
    'TOOL_PYTHON_IMAGE=docker.io/library/python:3.13.0-slim-bookworm' \
    > "$PROJECT_ROOT/versions/edge.env"
}

expect_unchanged_failure() {
  local expected=$1
  local records=$2
  shift 2
  cp "$PROJECT_ROOT/versions/edge.env" "$TEST_ROOT/before.env"
  if FAKE_VERSION_UPDATE_OUTPUT="$records" update_versions "$@" 2>"$TEST_ROOT/error"; then
    printf 'version update test: malformed helper output unexpectedly succeeded\n' >&2
    exit 1
  fi
  grep -Fq "$expected" "$TEST_ROOT/error"
  cmp -s "$PROJECT_ROOT/versions/edge.env" "$TEST_ROOT/before.env"
}

write_profile
output=$(FAKE_VERSION_UPDATE_OUTPUT=$'LATENTCRATE_VERSION_UPDATE|comfyui|COMFYUI_REF|v1.1.0\nLATENTCRATE_VERSION_RESULT|comfyui|1' \
  update_versions comfyui edge)
[[ "$output" == *'build version-update'* ]]
[[ "$output" == *'Updated 1 version pin(s) in profile edge.'* ]]
grep -Fxq 'COMFYUI_REF=v1.1.0' "$PROJECT_ROOT/versions/edge.env"

write_profile
output=$(FAKE_VERSION_UPDATE_OUTPUT=$'LATENTCRATE_VERSION_UPDATE|frontend|COMFYUI_FRONTEND_REF|Comfy-Org/ComfyUI_frontend@v1.3.0\nLATENTCRATE_VERSION_UPDATE|frontend|COMFY_FRONTEND_DIST_SHA256|bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\nLATENTCRATE_VERSION_UPDATE|pytorch|PYTORCH_DEVEL_IMAGE|docker.io/pytorch/pytorch:2.9.0-cuda13.0-cudnn9-devel\nLATENTCRATE_VERSION_UPDATE|pytorch|PYTORCH_RUNTIME_IMAGE|docker.io/pytorch/pytorch:2.9.0-cuda13.0-cudnn9-runtime\nLATENTCRATE_VERSION_RESULT|all|4' \
  update_versions all edge)
[[ "$output" == *'Updated 4 version pin(s) in profile edge.'* ]]
grep -Fxq 'COMFYUI_FRONTEND_REF=Comfy-Org/ComfyUI_frontend@v1.3.0' \
  "$PROJECT_ROOT/versions/edge.env"
grep -Fxq 'COMFY_FRONTEND_DIST_SHA256=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' \
  "$PROJECT_ROOT/versions/edge.env"
grep -Fxq 'PYTORCH_RUNTIME_IMAGE=docker.io/pytorch/pytorch:2.9.0-cuda13.0-cudnn9-runtime' \
  "$PROJECT_ROOT/versions/edge.env"

write_profile
output=$(FAKE_VERSION_UPDATE_OUTPUT='LATENTCRATE_VERSION_RESULT|node|0' \
  update_versions node edge)
[[ "$output" == *'already has the latest eligible node versions.'* ]]

write_profile
expect_unchanged_failure 'incomplete frontend update' \
  $'LATENTCRATE_VERSION_UPDATE|frontend|COMFYUI_FRONTEND_REF|Comfy-Org/ComfyUI_frontend@v1.3.0\nLATENTCRATE_VERSION_RESULT|frontend|1' \
  frontend edge

write_profile
expect_unchanged_failure 'invalid value for COMFY_FRONTEND_DIST_SHA256' \
  $'LATENTCRATE_VERSION_UPDATE|frontend|COMFYUI_FRONTEND_REF|Comfy-Org/ComfyUI_frontend@v1.3.0\nLATENTCRATE_VERSION_UPDATE|frontend|COMFY_FRONTEND_DIST_SHA256|bad\nLATENTCRATE_VERSION_RESULT|frontend|2' \
  frontend edge

write_profile
expect_unchanged_failure 'duplicate updates for COMFYUI_REF' \
  $'LATENTCRATE_VERSION_UPDATE|comfyui|COMFYUI_REF|v1.1.0\nLATENTCRATE_VERSION_UPDATE|comfyui|COMFYUI_REF|v1.2.0\nLATENTCRATE_VERSION_RESULT|comfyui|2' \
  comfyui edge

write_profile
exec {held_lock_fd}<"$PROJECT_ROOT/versions"
flock -n "$held_lock_fd"
if FAKE_VERSION_UPDATE_OUTPUT='LATENTCRATE_VERSION_RESULT|node|0' \
    update_versions node edge 2>"$TEST_ROOT/error"; then
  printf 'version update test: overlapping update unexpectedly succeeded\n' >&2
  exit 1
fi
grep -Fq 'another version profile update is running for profile edge' "$TEST_ROOT/error"
flock -u "$held_lock_fd"
exec {held_lock_fd}>&-

write_profile
cp "$PROJECT_ROOT/versions/edge.env" "$TEST_ROOT/before.env"
if FAKE_VERSION_PROFILE_TO_CHANGE="$PROJECT_ROOT/versions/edge.env" \
    FAKE_VERSION_UPDATE_OUTPUT='LATENTCRATE_VERSION_RESULT|node|0' \
    update_versions node edge 2>"$TEST_ROOT/error"; then
  printf 'version update test: concurrent profile edit was overwritten\n' >&2
  exit 1
fi
grep -Fq 'version profile changed while the version update was running' "$TEST_ROOT/error"
grep -Fxq 'CHANGED_DURING_RESOLUTION=true' "$PROJECT_ROOT/versions/edge.env"

if find "$PROJECT_ROOT/versions" -name '.edge.version-update.*' -print -quit | grep -q .; then
  printf 'version update test: temporary profile files remain\n' >&2
  exit 1
fi

printf 'LatentCrate version update checks passed.\n'
