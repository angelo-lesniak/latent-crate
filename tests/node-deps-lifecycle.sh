#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
TEST_PROJECT_ROOT=$(mktemp -d)
trap 'rm -rf -- "$TEST_PROJECT_ROOT"' EXIT

PROJECT_ROOT=$TEST_PROJECT_ROOT
# shellcheck source=lib/latentcrate/core.sh
source "$SOURCE_PROJECT_ROOT/lib/latentcrate/core.sh"
# shellcheck source=lib/latentcrate/node-deps.sh
source "$SOURCE_PROJECT_ROOT/lib/latentcrate/node-deps.sh"

mkdir -p "$PROJECT_ROOT/build/custom-node-requirements"
printf 'old\n' > "$PROJECT_ROOT/build/custom-node-requirements/value"
begin_node_dependency_candidate
rm -rf -- "$PROJECT_ROOT/build/custom-node-requirements"
mkdir -p "$PROJECT_ROOT/build/custom-node-requirements"
printf 'new\n' > "$PROJECT_ROOT/build/custom-node-requirements/value"
restore_node_dependency_candidate
[[ "$(< "$PROJECT_ROOT/build/custom-node-requirements/value")" == old ]]

begin_node_dependency_candidate
rm -rf -- "$PROJECT_ROOT/build/custom-node-requirements"
mkdir -p "$PROJECT_ROOT/build/custom-node-requirements"
printf 'new\n' > "$PROJECT_ROOT/build/custom-node-requirements/value"
write_node_dependency_candidate_state committed
restore_node_dependency_candidate
[[ "$(< "$PROJECT_ROOT/build/custom-node-requirements/value")" == new ]]

begin_node_dependency_candidate
write_node_dependency_candidate_state restoring
rm -rf -- "$PROJECT_ROOT/build/custom-node-requirements"
mv -- "$NODE_DEPS_CANDIDATE_BACKUP/snapshot" \
  "$PROJECT_ROOT/build/custom-node-requirements"
restore_node_dependency_candidate
[[ "$(< "$PROJECT_ROOT/build/custom-node-requirements/value")" == new ]]

COMFY_DATA_DIR="$PROJECT_ROOT/data"
COMFY_LOCAL_NODES_DIR="$PROJECT_ROOT/local"
mkdir -p "$COMFY_DATA_DIR/custom_nodes" "$COMFY_LOCAL_NODES_DIR"
printf 'last-valid\n' > "$PROJECT_ROOT/build/custom-node-requirements/manifest.txt"
profile_file() { printf '%s\n' fixture; }
validate_custom_node_roots() { :; }
detect_engine() { printf '%s\n' docker; }
compose() { return 1; }
if snapshot_node_dependencies current; then
  printf 'node dependency lifecycle test: failed helper was treated as successful\n' >&2
  exit 1
fi
grep -Fxq last-valid "$PROJECT_ROOT/build/custom-node-requirements/manifest.txt"

NODE_DEPS_CANDIDATE_BACKUP=$(mktemp -d "$PROJECT_ROOT/build/.node-deps-buildable.XXXXXX")
mkdir -p "$NODE_DEPS_CANDIDATE_BACKUP/snapshot"
printf 'partial\n' > "$NODE_DEPS_CANDIDATE_BACKUP/snapshot/value"
restore_node_dependency_candidate
grep -Fxq last-valid "$PROJECT_ROOT/build/custom-node-requirements/manifest.txt"

first_key=$(bash "$SOURCE_PROJECT_ROOT/scripts/custom-node-cache-key.sh" \
  devel runtime comfy '' https://example.invalid/simple 12.0)
printf 'changed snapshot\n' > "$PROJECT_ROOT/build/custom-node-requirements/manifest.txt"
second_key=$(bash "$SOURCE_PROJECT_ROOT/scripts/custom-node-cache-key.sh" \
  devel runtime comfy '' https://example.invalid/simple 12.0)
[[ "$first_key" == "$second_key" ]]

printf 'LatentCrate third-party node dependency lifecycle checks passed.\n'
