#!/usr/bin/env bash
# Engine-free tests for the pure-Bash policy logic in
# services/comfy/entrypoint.sh.
#
# The frontend-mode policy runs before the entrypoint touches any container
# path, so those cases execute the real script end to end and assert on its
# rejection messages. The custom-node rejection helpers run only after the
# hardcoded /data and /local roots have been prepared, so the suite extracts
# those function definitions verbatim from the real script (no copied logic)
# and executes them against temporary directories.
set -Eeuo pipefail

PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
readonly PROJECT_ROOT
ENTRYPOINT="$PROJECT_ROOT/services/comfy/entrypoint.sh"

TEST_ROOT=$(mktemp -d)
trap 'rm -rf -- "$TEST_ROOT"' EXIT

# --- Frontend-mode policy: run the real entrypoint; every case below must be
# --- rejected before the script performs any filesystem work.

expect_entrypoint_rejection() {
  local label=$1
  local expected=$2
  shift 2
  local output status=0

  output=$(env -i PATH="$PATH" "$@" bash "$ENTRYPOINT" 2>&1) || status=$?
  if ((status == 0)); then
    printf 'entrypoint test [%s]: the entrypoint unexpectedly accepted this configuration\n' \
      "$label" >&2
    exit 1
  fi
  if [[ "$output" != *"$expected"* ]]; then
    printf 'entrypoint test [%s]: expected %q in output:\n%s\n' \
      "$label" "$expected" "$output" >&2
    exit 1
  fi
}

expect_entrypoint_rejection 'git mode on release image' \
  'requested frontend mode git does not match image mode release' \
  COMFY_FRONTEND_MODE=git
expect_entrypoint_rejection 'release mode on git image' \
  'requested frontend mode release does not match image mode git' \
  COMFY_FRONTEND_MODE=release LATENTCRATE_FRONTEND_IMAGE_MODE=git
expect_entrypoint_rejection 'image mode with a foreign root' \
  'image frontend mode must use /opt/latentcrate-frontend' \
  COMFY_FRONTEND_MODE=release COMFY_FRONTEND_ROOT=/somewhere/else
expect_entrypoint_rejection 'dist mode without the fixed mount' \
  'dist frontend mode must use the read-only mount at /opt/latentcrate-frontend-dist' \
  COMFY_FRONTEND_MODE=dist
expect_entrypoint_rejection 'unsupported mode' \
  'unsupported frontend mode: cdn' \
  COMFY_FRONTEND_MODE=cdn

# --- Custom-node policy: extract the real helper functions and drive them
# --- against temporary managed/local roots.

extract_function() {
  local name=$1
  awk -v fn="$name" '
    $0 == fn "() {" { found = 1 }
    found { print }
    found && $0 == "}" { exit }
  ' "$ENTRYPOINT"
}

duplicate_function=$(extract_function reject_duplicate_custom_nodes)
hidden_function=$(extract_function reject_hidden_custom_nodes)
for extracted in "$duplicate_function" "$hidden_function"; do
  if [[ "$extracted" != *'exit 1'* ]]; then
    printf 'entrypoint test: could not extract the rejection helpers from %s\n' \
      "$ENTRYPOINT" >&2
    exit 1
  fi
done

runner="$TEST_ROOT/reject-runner.sh"
# The generated runner script must contain literal $1 and $2; they expand
# later when the runner itself executes.
# shellcheck disable=SC2016
{
  printf 'set -uo pipefail\n'
  printf 'DATA_ROOT=$1\n'
  printf 'LOCAL_NODES_ROOT=$2\n'
  printf '%s\n' "$hidden_function"
  printf '%s\n' "$duplicate_function"
  printf 'reject_hidden_custom_nodes\n'
  printf 'reject_duplicate_custom_nodes\n'
} > "$runner"

scenario=0
run_reject_checks() {
  # Prints the helper output; returns the helper exit status. $data_root and
  # $local_root point at the scenario's fresh managed/local custom-node roots.
  bash "$runner" "$data_root" "$local_root" 2>&1
}

new_scenario() {
  scenario=$((scenario + 1))
  data_root="$TEST_ROOT/scenario-$scenario/data"
  local_root="$TEST_ROOT/scenario-$scenario/local"
  mkdir -p "$data_root/custom_nodes" "$local_root"
}

expect_reject_pass() {
  local label=$1
  local output status=0
  output=$(run_reject_checks) || status=$?
  if ((status != 0)); then
    printf 'entrypoint test [%s]: expected acceptance, got rejection:\n%s\n' \
      "$label" "$output" >&2
    exit 1
  fi
}

expect_reject_failure() {
  local label=$1
  local expected=$2
  local output status=0
  output=$(run_reject_checks) || status=$?
  if ((status == 0)); then
    printf 'entrypoint test [%s]: expected rejection, but the checks passed\n' \
      "$label" >&2
    exit 1
  fi
  if [[ "$output" != *"$expected"* ]]; then
    printf 'entrypoint test [%s]: expected %q in output:\n%s\n' \
      "$label" "$expected" "$output" >&2
    exit 1
  fi
}

new_scenario
mkdir -p "$data_root/custom_nodes/managed-node" "$local_root/local-node"
expect_reject_pass 'distinct managed and local nodes'

new_scenario
mkdir -p "$data_root/custom_nodes/SharedNode" "$local_root/sharednode"
expect_reject_failure 'case-insensitive duplicate node' \
  'duplicate custom-node name in managed and local sources: SharedNode / sharednode'

new_scenario
mkdir -p "$data_root/custom_nodes/shared.disabled" "$local_root/shared"
expect_reject_pass 'disabled managed copy does not count as a duplicate'

new_scenario
mkdir -p "$data_root/custom_nodes/.sneaky-node"
expect_reject_failure 'hidden managed custom-node directory' \
  'hidden custom-node directories are not supported'

new_scenario
mkdir -p "$local_root/.sneaky-node"
expect_reject_failure 'hidden local custom-node directory' \
  'hidden custom-node directories are not supported'

new_scenario
mkdir -p "$data_root/custom_nodes/.latentcrate-node-set-pending.disabled"
expect_reject_failure 'interrupted node-set transaction' \
  'interrupted custom-node set transaction detected'

new_scenario
touch "$data_root/custom_nodes/.gitkeep" "$local_root/.gitkeep"
expect_reject_pass 'hidden files (not directories) are tolerated'

# --- Sage-mode policy: extract the real argument helper and drive each mode.

sage_function=$(extract_function append_sage_args)
if [[ "$sage_function" != *'use-sage-attention'* ]]; then
  printf 'entrypoint test: could not extract append_sage_args from %s\n' \
    "$ENTRYPOINT" >&2
  exit 1
fi

sage_runner="$TEST_ROOT/sage-runner.sh"
# The generated runner prints the final args array on one exact line.
# shellcheck disable=SC2016
{
  printf 'set -uo pipefail\n'
  printf 'args=()\n'
  printf '%s\n' "$sage_function"
  printf 'append_sage_args\n'
  printf 'printf "sage-args:%%s\\n" "${args[*]-}"\n'
} > "$sage_runner"

expect_sage_args() {
  local label=$1
  local expected=$2
  shift 2
  local output status=0
  output=$(env -i PATH="$PATH" "$@" bash "$sage_runner" 2>&1) || status=$?
  if ((status != 0)); then
    printf 'entrypoint test [%s]: expected acceptance, got rejection:\n%s\n' \
      "$label" "$output" >&2
    exit 1
  fi
  if [[ "$output" != "sage-args:$expected" ]]; then
    printf 'entrypoint test [%s]: expected sage-args:%q, got:\n%s\n' \
      "$label" "$expected" "$output" >&2
    exit 1
  fi
}

expect_sage_rejection() {
  local label=$1
  local expected=$2
  shift 2
  local output status=0
  output=$(env -i PATH="$PATH" "$@" bash "$sage_runner" 2>&1) || status=$?
  if ((status == 0)); then
    printf 'entrypoint test [%s]: the Sage helper unexpectedly accepted this configuration\n' \
      "$label" >&2
    exit 1
  fi
  if [[ "$output" != *"$expected"* ]]; then
    printf 'entrypoint test [%s]: expected %q in output:\n%s\n' \
      "$label" "$expected" "$output" >&2
    exit 1
  fi
}

expect_sage_args 'default mode adds no Sage argument' ''
expect_sage_args 'off mode adds no Sage argument' '' \
  LATENTCRATE_SAGE_MODE=off
expect_sage_args 'global mode on a Sage image adds --use-sage-attention' \
  '--use-sage-attention' LATENTCRATE_SAGE_MODE=global LATENTCRATE_SAGE_ENABLED=1
expect_sage_rejection 'global mode on a non-Sage image' \
  'requires the runtime-sage image target' LATENTCRATE_SAGE_MODE=global
expect_sage_rejection 'unknown Sage mode' \
  'must be off, available, or global' LATENTCRATE_SAGE_MODE=maybe

printf 'LatentCrate entrypoint policy checks passed.\n'
