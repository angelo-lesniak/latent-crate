#!/usr/bin/env bash
# Engine-free tests for scripts/doctor.sh using fake binaries from
# tests/fixtures/doctor-bin. Each case runs the real doctor with a sanitized
# PATH (fakes first, then core utilities only) and a scrubbed environment so
# results do not depend on the host's engines, GPU, or free disk space.
set -Eeuo pipefail

PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
readonly PROJECT_ROOT
DOCTOR="$PROJECT_ROOT/scripts/doctor.sh"
FAKES="$PROJECT_ROOT/tests/fixtures/doctor-bin"

TEST_ROOT=$(mktemp -d)
trap 'rm -rf -- "$TEST_ROOT"' EXIT

WORKDIR="$TEST_ROOT/work"
STORAGE_ROOT="$TEST_ROOT/storage"
mkdir -p "$WORKDIR" "$STORAGE_ROOT"

bin_full="$TEST_ROOT/bin-full"
bin_no_gpu="$TEST_ROOT/bin-no-gpu"
mkdir -p "$bin_full" "$bin_no_gpu"
for fake in podman docker nvidia-smi nvidia-ctk uname df ss flock; do
  cp "$FAKES/$fake" "$bin_full/$fake"
done
for fake in podman docker uname df ss flock; do
  cp "$FAKES/$fake" "$bin_no_gpu/$fake"
done
chmod +x "$bin_full"/* "$bin_no_gpu"/*

doctor_output=
doctor_status=0
case_label=

run_doctor() {
  local bin_dir=$1
  shift
  local -a environment=()
  while [[ "${1:-}" == *=* ]]; do
    environment+=("$1")
    shift
  done
  doctor_status=0
  doctor_output=$(cd "$WORKDIR" && env -i \
    PATH="$bin_dir:/usr/bin:/bin" \
    FAKE_PODMAN_GRAPH_ROOT="$STORAGE_ROOT" \
    FAKE_DOCKER_ROOT_DIR="$STORAGE_ROOT" \
    "${environment[@]}" \
    bash "$DOCTOR" "$@" 2>&1) || doctor_status=$?
}

fail_case() {
  printf 'doctor test [%s]: %s\n' "$case_label" "$1" >&2
  printf '%s\n' "$doctor_output" >&2
  exit 1
}

expect_output() {
  local expected=$1
  [[ "$doctor_output" == *"$expected"* ]] \
    || fail_case "expected output to contain: $expected"
}

reject_output() {
  local unexpected=$1
  [[ "$doctor_output" != *"$unexpected"* ]] \
    || fail_case "expected output not to contain: $unexpected"
}

expect_success() {
  ((doctor_status == 0)) || fail_case "expected exit 0, got $doctor_status"
}

expect_failure_status() {
  ((doctor_status != 0)) || fail_case 'expected a nonzero exit status'
}

# --- Engine detection: healthy rootless Podman, matching GPU, plenty of disk.
case_label='healthy podman'
run_doctor "$bin_full" \
  --engine podman --custom-node-arch-list 12.0 --sage-arch-list 12.0
expect_success
expect_output '[ok] podman engine is reachable'
expect_output '[ok] Podman engine is rootless'
expect_output '[ok] Podman uses crun, which supports keep-groups'
expect_output '[ok] checking the smaller image without SageAttention'
expect_output 'custom-node CUDA architecture list covers compute capability 12.0'
expect_output 'SageAttention CUDA architecture list covers compute capability 12.0'
expect_output "engine storage at $STORAGE_ROOT has 100 GB free"
expect_output '0 failure(s)'

# --- Engine detection: requested engine exists but is unreachable.
case_label='unreachable podman'
run_doctor "$bin_full" FAKE_PODMAN_INFO_FAIL=true \
  --engine podman --custom-node-arch-list 12.0 --sage-arch-list 12.0
expect_failure_status
expect_output '[fail] podman engine is not reachable'

# --- Engine detection: automatic selection with no usable engine at all.
case_label='no usable engine'
run_doctor "$bin_full" FAKE_PODMAN_INFO_FAIL=true FAKE_DOCKER_INFO_FAIL=true \
  --custom-node-arch-list 12.0 --sage-arch-list 12.0
expect_failure_status
expect_output '[fail] neither Podman nor Docker has both a reachable engine and Compose provider'

# --- Engine policy: rootful Podman is rejected.
case_label='rootful podman'
run_doctor "$bin_full" FAKE_PODMAN_ROOTLESS=false \
  --engine podman --custom-node-arch-list 12.0 --sage-arch-list 12.0
expect_failure_status
expect_output '[fail] the supported Podman path requires a rootless engine'

# --- Engine detection: explicit Docker selection uses the Docker probes.
case_label='healthy docker'
run_doctor "$bin_full" \
  --engine docker --custom-node-arch-list 12.0 --sage-arch-list 12.0
expect_success
expect_output '[ok] docker engine is reachable'
expect_output "engine storage at $STORAGE_ROOT has 100 GB free"

# --- GPU capability: the architecture lists do not cover the detected GPU.
case_label='gpu arch mismatch'
run_doctor "$bin_full" FAKE_COMPUTE_CAP=8.6 \
  --engine podman --custom-node-arch-list 12.0 --sage-arch-list 12.0 \
  --sage true
expect_failure_status
expect_output '[fail] CUSTOM_NODE_CUDA_ARCH_LIST=12.0 does not cover compute capability 8.6'
expect_output '[fail] SAGE_CUDA_ARCH_LIST=12.0 does not cover compute capability 8.6'

# --- GPU capability: semicolon-separated lists and +PTX suffixes match.
case_label='gpu arch list formats'
run_doctor "$bin_full" FAKE_COMPUTE_CAP=8.6 \
  --engine podman --custom-node-arch-list '8.6+PTX;12.0' --sage-arch-list '8.6;12.0'
expect_success
expect_output 'custom-node CUDA architecture list covers compute capability 8.6'

# --- GPU capability: Sage list mismatch is only a warning by default.
case_label='default sage disabled mismatch'
run_doctor "$bin_full" \
  --engine podman --custom-node-arch-list 12.0 --sage-arch-list 9.0
expect_success
expect_output 'the Sage image is disabled'

# --- GPU tooling: a missing GPU stack fails by default...
case_label='missing gpu stack'
run_doctor "$bin_no_gpu" \
  --engine podman --custom-node-arch-list 12.0 --sage-arch-list 12.0
expect_failure_status
expect_output '[fail] nvidia-smi was not found; the supported GPU path is unavailable'
expect_output '[fail] nvidia-ctk was not found'

# --- ...but --allow-no-gpu downgrades the same findings to warnings.
case_label='allow-no-gpu'
run_doctor "$bin_no_gpu" \
  --engine podman --allow-no-gpu true \
  --custom-node-arch-list 12.0 --sage-arch-list 12.0
expect_success
expect_output '[warn] nvidia-smi was not found; the supported GPU path is unavailable (--allow-no-gpu was supplied)'

# --- Driver policy: drivers older than the requested major fail.
case_label='old driver'
run_doctor "$bin_full" FAKE_DRIVER_VERSION=570.12 \
  --engine podman --min-driver-major 580 \
  --custom-node-arch-list 12.0 --sage-arch-list 12.0
expect_failure_status
expect_output '[fail] NVIDIA driver 570.12 is older than the CUDA 13.x minimum major 580'

# --- Driver policy: a lowered minimum accepts the same driver.
case_label='lowered driver minimum'
run_doctor "$bin_full" FAKE_DRIVER_VERSION=570.12 \
  --engine podman --min-driver-major 570 \
  --custom-node-arch-list 12.0 --sage-arch-list 12.0
expect_success
expect_output 'NVIDIA driver 570.12 satisfies CUDA 13.x minimum major 570'

# --- Disk space: under 75 GB free warns but does not fail.
case_label='low disk space'
run_doctor "$bin_full" FAKE_DF_FREE_KB=10485760 \
  --engine podman --custom-node-arch-list 12.0 --sage-arch-list 12.0
expect_success
expect_output "engine storage at $STORAGE_ROOT has only 10 GB free"

# --- Port check: a busy ComfyUI port is reported as a warning.
case_label='busy port'
run_doctor "$bin_full" FAKE_SS_PORT_BUSY=true \
  --engine podman --custom-node-arch-list 12.0 --sage-arch-list 12.0
expect_success
expect_output '[warn] host port 4207 is already in use'
case_label='free port'
run_doctor "$bin_full" \
  --engine podman --custom-node-arch-list 12.0 --sage-arch-list 12.0
reject_output 'already in use'

printf 'LatentCrate doctor checks passed.\n'
