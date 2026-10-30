#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
readonly PROJECT_ROOT
cd "$PROJECT_ROOT"

command -v podman-compose >/dev/null 2>&1 \
  || { printf 'podman-compose compatibility: podman-compose is required\n' >&2; exit 1; }

unset FAKE_PODMAN_LOG

# The settings and the values bin/latentcrate derives; compose.yaml requires
# every one of them.
set -a
# shellcheck disable=SC1091
source .env.example
set +a
export HOST_UID=1000 HOST_GID=1000
export COMFY_FRONTEND_MODE=release
export COMFY_BUILD_TARGET=runtime
export LATENTCRATE_IMAGE=latentcrate/comfy:release-fixture
export FRONTEND_GIT_URL=https://github.com/Comfy-Org/ComfyUI_frontend.git
export FRONTEND_GIT_REF=0000000000000000000000000000000000000000
export FRONTEND_GIT_REQUESTED_REF=fixture
export NODE_SET_FILE="$PROJECT_ROOT/config/custom-nodes/sets/latent-nodepack.toml"
export NODE_SET_TARGET_DIR="$PROJECT_ROOT/data/comfy/custom_nodes"
export NODE_DEPS_SOURCE_DIR="$PROJECT_ROOT/data/comfy/custom_nodes"
export NODE_DEPS_LOCAL_SOURCE_DIR="$PROJECT_ROOT/local/custom_nodes"
export NODE_DEPS_OUTPUT_DIR="$PROJECT_ROOT/build"
export FRONTEND_SOURCE_DIR="$PROJECT_ROOT/tests/fixtures/frontend-source"
export FRONTEND_OUTPUT_DIR="$PROJECT_ROOT/data/cache/frontend-builds/test"
export FRONTEND_PNPM_CACHE_DIR="$PROJECT_ROOT/data/cache/frontend-pnpm"
export FRONTEND_WORK_DIR="$PROJECT_ROOT/data/cache/frontend-work/test"
export TEMPLATE_DRAFT_OUTPUT_DIR="$PROJECT_ROOT/build/model-set-drafts"
network_log="$PROJECT_ROOT/build/podman-compose-network.log"
rm -f -- "$network_log"
cleanup() {
  rm -f -- "$network_log"
}
trap cleanup EXIT

mkdir -p \
  "$NODE_SET_TARGET_DIR" \
  "$NODE_DEPS_LOCAL_SOURCE_DIR" \
  "$NODE_DEPS_OUTPUT_DIR" \
  "$FRONTEND_OUTPUT_DIR" \
  "$FRONTEND_PNPM_CACHE_DIR" \
  "$FRONTEND_WORK_DIR" \
  "$TEMPLATE_DRAFT_OUTPUT_DIR"

compose_files=(--file compose.yaml --file compose.podman.yaml)
runtime_files=("${compose_files[@]}" --file compose.gpu.yaml)
if [[ "${OS:-}" == Windows_NT ]]; then
  fake_engine=$(cygpath -w "$PROJECT_ROOT/tests/fixtures/podman-compose-bin/podman.cmd")
  fake_network_log=$(cygpath -w "$network_log")
else
  fake_engine="$PROJECT_ROOT/tests/fixtures/podman-compose-bin/podman"
  fake_network_log=$network_log
  chmod +x "$fake_engine"
fi

podman_compose_dry_run() {
  podman-compose "${runtime_files[@]}" \
    --env-file pins.env \
    --podman-path "$fake_engine" \
    --dry-run "$@" >/dev/null
}

podman_compose_tool_dry_run() {
  podman-compose "${compose_files[@]}" \
    --env-file pins.env \
    --profile tools \
    --podman-path "$fake_engine" \
    --dry-run "$@" >/dev/null
}

podman-compose "${runtime_files[@]}" \
  --env-file pins.env \
  --podman-path "$fake_engine" config >/dev/null

COMFY_FRONTEND_MODE=git \
COMFY_BUILD_TARGET=runtime-sage \
LATENTCRATE_IMAGE=latentcrate/comfy:git-sage-fixture \
LATENTCRATE_SAGE=available \
  podman-compose "${runtime_files[@]}" \
    --env-file pins.env \
    --podman-path "$fake_engine" config >/dev/null

COMFY_FRONTEND_MODE=dist \
COMFY_BUILD_TARGET=comfy-runtime \
LATENTCRATE_IMAGE=latentcrate/comfy:dist-fixture \
COMFY_FRONTEND_DIST_DIR="$PROJECT_ROOT/tests/fixtures/frontend-dist" \
  podman-compose "${runtime_files[@]}" \
    --file compose.frontend-dist.yaml \
    --env-file pins.env \
    --podman-path "$fake_engine" config >/dev/null

tool_services=$(podman-compose "${compose_files[@]}" \
  --env-file pins.env --profile tools \
  --podman-path "$fake_engine" config --services)
for service in \
  comfy \
  node-deps-snapshot \
  node-set \
  node-set-status \
  model-set \
  model-set-status \
  template-inspector \
  template-draft \
  frontend-fetch \
  frontend-build; do
  grep -Fxq "$service" <<< "$tool_services" \
    || { printf 'podman-compose compatibility: tools profile misses %s\n' "$service" >&2; exit 1; }
done

# `--dry-run build` resolves the selected profiled service without contacting
# a Podman engine. This catches the provider behavior that originally reported
# a directly targeted helper as missing.
for service in \
  node-deps-snapshot \
  node-set \
  node-set-status \
  model-set \
  model-set-status \
  frontend-fetch \
  frontend-build; do
  podman_compose_tool_dry_run build "$service"
done

# Generate Podman arguments for every helper and user-facing runtime command.
# The fixture answers only the engine probes that podman-compose performs even
# in dry-run mode; no container engine or network is used.
for service in \
  node-deps-snapshot \
  node-set \
  node-set-status \
  frontend-fetch \
  frontend-build; do
  podman_compose_tool_dry_run run --rm --no-deps "$service"
done
for service in model-set model-set-status; do
  podman_compose_tool_dry_run run --rm --no-deps -T "$service" status all
done
podman_compose_tool_dry_run run --rm --no-deps -T template-inspector list
podman_compose_tool_dry_run run --rm --no-deps -T template-draft \
  create-model-set fixture --name fixture

(
  export FAKE_PODMAN_LOG=$fake_network_log
  podman-compose "${runtime_files[@]}" \
    --env-file pins.env \
    --podman-path "$fake_engine" \
    run --rm --no-deps comfy >/dev/null
)
grep -Fq -- '--network=latentcrate_default:alias=comfy' "$network_log" \
  || { printf 'podman-compose compatibility: comfy does not use the default network\n' >&2; exit 1; }

podman_compose_dry_run build comfy
podman_compose_dry_run up --no-build --detach comfy
podman_compose_dry_run up --no-build --force-recreate --detach comfy
podman_compose_dry_run down
podman_compose_dry_run ps
podman_compose_dry_run logs --follow comfy
podman_compose_dry_run exec comfy bash

printf 'LatentCrate podman-compose 1.6 compatibility checks passed.\n'
