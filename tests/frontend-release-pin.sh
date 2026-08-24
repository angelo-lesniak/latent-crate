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
# shellcheck source=lib/latentcrate/frontend.sh
source "$SOURCE_PROJECT_ROOT/lib/latentcrate/frontend.sh"

fake_bin="$TEST_ROOT/fake-bin"
mkdir -p "$fake_bin"
cp "$SOURCE_PROJECT_ROOT/tests/fixtures/fake-bin/docker" "$fake_bin/docker"
chmod +x "$fake_bin/docker"
export PATH="$fake_bin:$PATH"
export CONTAINER_ENGINE=docker

write_profile() {
  printf '%s\n' \
    'COMFYUI_FRONTEND_REF=Comfy-Org/ComfyUI_frontend@v1.2.3' \
    'COMFY_FRONTEND_DIST_SHA256=' \
    > "$PROJECT_ROOT/versions/edge.env"
}

expected_digest=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
write_profile
output=$(FAKE_FRONTEND_RELEASE_DIGEST=$expected_digest pin_frontend_release edge)
[[ "$output" == *'build frontend-release-pin'* ]]
[[ "$output" == *"Pinned Comfy-Org/ComfyUI_frontend@v1.2.3 dist.zip for profile edge: $expected_digest"* ]]
grep -Fxq "COMFY_FRONTEND_DIST_SHA256=$expected_digest" \
  "$PROJECT_ROOT/versions/edge.env"
if find "$PROJECT_ROOT/versions" -name '.edge.frontend-pin.*' -print -quit | grep -q .; then
  printf 'frontend release pin test: temporary profile files remain\n' >&2
  exit 1
fi

write_profile
cp "$PROJECT_ROOT/versions/edge.env" "$TEST_ROOT/before-invalid.env"
if FAKE_FRONTEND_RELEASE_DIGEST=invalid pin_frontend_release edge 2>"$TEST_ROOT/error"; then
  printf 'frontend release pin test: invalid helper digest unexpectedly succeeded\n' >&2
  exit 1
fi
grep -Fq 'frontend release helper returned an invalid SHA-256' "$TEST_ROOT/error"
cmp -s "$PROJECT_ROOT/versions/edge.env" "$TEST_ROOT/before-invalid.env"

write_profile
exec {held_lock_fd}<"$PROJECT_ROOT/versions"
flock -n "$held_lock_fd"
if FAKE_FRONTEND_RELEASE_DIGEST=$expected_digest pin_frontend_release edge \
    2>"$TEST_ROOT/error"; then
  printf 'frontend release pin test: overlapping pin unexpectedly succeeded\n' >&2
  exit 1
fi
grep -Fq 'another version profile update is running' \
  "$TEST_ROOT/error"
grep -Fxq 'COMFY_FRONTEND_DIST_SHA256=' "$PROJECT_ROOT/versions/edge.env"
flock -u "$held_lock_fd"
exec {held_lock_fd}>&-

write_profile
if FAKE_FRONTEND_RELEASE_DIGEST=$expected_digest \
    FAKE_FRONTEND_PROFILE_TO_CHANGE="$PROJECT_ROOT/versions/edge.env" \
    pin_frontend_release edge 2>"$TEST_ROOT/error"; then
  printf 'frontend release pin test: concurrent profile update was overwritten\n' >&2
  exit 1
fi
grep -Fq 'version profile changed while the frontend release pin was running' \
  "$TEST_ROOT/error"
grep -Fxq 'CHANGED_DURING_DOWNLOAD=true' "$PROJECT_ROOT/versions/edge.env"
grep -Fxq 'COMFY_FRONTEND_DIST_SHA256=' "$PROJECT_ROOT/versions/edge.env"

write_profile
printf '%s\n' 'COMFY_FRONTEND_DIST_SHA256=duplicate' \
  >> "$PROJECT_ROOT/versions/edge.env"
if FAKE_FRONTEND_RELEASE_DIGEST=$expected_digest pin_frontend_release edge \
    2>"$TEST_ROOT/error"; then
  printf 'frontend release pin test: duplicate digest assignment unexpectedly succeeded\n' >&2
  exit 1
fi
grep -Fq 'must define COMFY_FRONTEND_DIST_SHA256 exactly once' "$TEST_ROOT/error"

printf 'LatentCrate frontend release pin checks passed.\n'
