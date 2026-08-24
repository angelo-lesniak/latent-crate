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

python_components=$(python "$SOURCE_PROJECT_ROOT/services/tools/update-versions.py" list)
bash_components=$(printf '%s\n' "${VERSION_COMPONENTS[@]}")
[[ "$bash_components" == "$python_components" ]] \
  || { printf 'version update test: Bash and Python component lists differ\n' >&2; exit 1; }

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

write_sparse_comfyui_profile() {
  printf '%s\n' \
    'COMFYUI_REF=v1.0.0' \
    'FRONTEND_NODE_IMAGE=docker.io/library/node:24.0.0-bookworm-slim' \
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

write_sparse_comfyui_profile
output=$(FAKE_VERSION_UPDATE_OUTPUT=$'LATENTCRATE_VERSION_UPDATE|comfyui|COMFYUI_REF|v1.1.0\nLATENTCRATE_VERSION_RESULT|comfyui|1' \
  update_versions comfyui edge)
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
expect_unchanged_failure 'update count did not match' \
  $'LATENTCRATE_VERSION_UPDATE|comfyui|COMFYUI_REF|v1.1.0\nLATENTCRATE_VERSION_RESULT|comfyui|0' \
  comfyui edge

write_profile
expect_unchanged_failure 'mismatched PyTorch image pair' \
  $'LATENTCRATE_VERSION_UPDATE|pytorch|PYTORCH_DEVEL_IMAGE|docker.io/pytorch/pytorch:2.9.0-cuda13.0-cudnn9-devel\nLATENTCRATE_VERSION_UPDATE|pytorch|PYTORCH_RUNTIME_IMAGE|docker.io/pytorch/pytorch:2.10.0-cuda13.0-cudnn9-runtime\nLATENTCRATE_VERSION_RESULT|pytorch|2' \
  pytorch edge

write_profile
expect_unchanged_failure 'unexpected component: ffmpeg' \
  $'LATENTCRATE_VERSION_UPDATE|ffmpeg|FFMPEG_REF|n7.1.0\nLATENTCRATE_VERSION_RESULT|comfyui|1' \
  comfyui edge

write_profile
expect_unchanged_failure 'unexpected update key: comfyui/FFMPEG_REF' \
  $'LATENTCRATE_VERSION_UPDATE|comfyui|FFMPEG_REF|n7.1.0\nLATENTCRATE_VERSION_RESULT|comfyui|1' \
  comfyui edge

write_profile
expect_unchanged_failure 'unknown protocol record' \
  $'LATENTCRATE_VERSION_UNKNOWN|comfyui|0\nLATENTCRATE_VERSION_RESULT|comfyui|0' \
  comfyui edge

write_profile
expect_unchanged_failure 'exactly one result record' \
  $'LATENTCRATE_VERSION_RESULT|comfyui|0\nLATENTCRATE_VERSION_RESULT|comfyui|0' \
  comfyui edge

write_profile
sed -i -e 's/^COMFYUI_REF=.*/COMFYUI_REF/' "$PROJECT_ROOT/versions/edge.env"
cp "$PROJECT_ROOT/versions/edge.env" "$TEST_ROOT/before.env"
if FAKE_VERSION_UPDATE_OUTPUT=$'LATENTCRATE_VERSION_UPDATE|comfyui|COMFYUI_REF|v1.1.0\nLATENTCRATE_VERSION_RESULT|comfyui|1' \
    update_versions comfyui edge 2>"$TEST_ROOT/error"; then
  printf 'version update test: bare profile key was reported as updated\n' >&2
  exit 1
fi
grep -Fq 'must define COMFYUI_REF exactly once' "$TEST_ROOT/error"
cmp -s "$PROJECT_ROOT/versions/edge.env" "$TEST_ROOT/before.env"

write_profile
mv "$PROJECT_ROOT/versions/edge.env" "$TEST_ROOT/symlink-target.env"
ln -s "$TEST_ROOT/symlink-target.env" "$PROJECT_ROOT/versions/edge.env"
if FAKE_VERSION_UPDATE_OUTPUT='LATENTCRATE_VERSION_RESULT|comfyui|0' \
    update_versions comfyui edge 2>"$TEST_ROOT/error"; then
  printf 'version update test: symbolic-link profile unexpectedly succeeded\n' >&2
  exit 1
fi
grep -Fq 'refusing symbolic link for version profile' "$TEST_ROOT/error"
rm -f -- "$PROJECT_ROOT/versions/edge.env"

write_profile
snapshot=$(snapshot_version_profile \
  "$PROJECT_ROOT/versions/edge.env" "$PROJECT_ROOT/versions/.edge.publish-test.XXXXXX")
staged=$(stage_version_profile_update \
  "$PROJECT_ROOT/versions/edge.env" "$snapshot" "$PROJECT_ROOT/versions/.edge.publish-test.XXXXXX")
mv "$PROJECT_ROOT/versions/edge.env" "$TEST_ROOT/publish-target.env"
ln -s "$TEST_ROOT/publish-target.env" "$PROJECT_ROOT/versions/edge.env"
if (publish_version_profile_update \
    "$PROJECT_ROOT/versions/edge.env" "$snapshot" "$staged" 'the test update') \
    2>"$TEST_ROOT/error"; then
  printf 'version update test: symbolic-link profile publication unexpectedly succeeded\n' >&2
  exit 1
fi
grep -Fq 'refusing symbolic link for version profile' "$TEST_ROOT/error"
rm -f -- "$PROJECT_ROOT/versions/edge.env" "$snapshot" "$staged"

write_profile
exec {held_lock_fd}<"$PROJECT_ROOT/versions"
flock -n "$held_lock_fd"
if FAKE_VERSION_UPDATE_OUTPUT='LATENTCRATE_VERSION_RESULT|node|0' \
    update_versions node edge 2>"$TEST_ROOT/error"; then
  printf 'version update test: overlapping update unexpectedly succeeded\n' >&2
  exit 1
fi
grep -Fq 'another version profile update is running' "$TEST_ROOT/error"
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
