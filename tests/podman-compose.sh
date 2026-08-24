#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
readonly PROJECT_ROOT
cd "$PROJECT_ROOT"

command -v podman-compose >/dev/null 2>&1 \
  || { printf 'podman-compose compatibility: podman-compose is required\n' >&2; exit 1; }

unset \
  COMFY_BUILD_TARGET \
  COMFY_FRONTEND_DIST_DIR \
  COMFY_FRONTEND_MODE \
  FRONTEND_GIT_REF \
  FRONTEND_GIT_REQUESTED_REF \
  FRONTEND_GIT_URL

unset CUSTOM_NODE_CACHE_KEY
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

mkdir -p \
  "$NODE_SET_TARGET_DIR" \
  "$NODE_DEPS_LOCAL_SOURCE_DIR" \
  "$NODE_DEPS_OUTPUT_DIR" \
  "$FRONTEND_OUTPUT_DIR" \
  "$FRONTEND_PNPM_CACHE_DIR" \
  "$FRONTEND_WORK_DIR" \
  "$TEMPLATE_DRAFT_OUTPUT_DIR"

compose_files=(--file compose.yaml --file compose.podman.yaml)
if [[ "${OS:-}" == Windows_NT ]]; then
  fake_engine=$(cygpath -w "$PROJECT_ROOT/tests/fixtures/podman-compose-bin/podman.cmd")
else
  fake_engine="$PROJECT_ROOT/tests/fixtures/podman-compose-bin/podman"
  chmod +x "$fake_engine"
fi

podman_compose_dry_run() {
  podman-compose "${compose_files[@]}" \
    --env-file versions/edge.env \
    --podman-path "$fake_engine" \
    --dry-run "$@" >/dev/null
}

podman_compose_tool_dry_run() {
  podman-compose "${compose_files[@]}" \
    --env-file versions/edge.env \
    --profile tools \
    --podman-path "$fake_engine" \
    --dry-run "$@" >/dev/null
}

for profile in current edge; do
  podman-compose "${compose_files[@]}" \
    --env-file "versions/${profile}.env" \
    --podman-path "$fake_engine" config >/dev/null
done

COMFY_FRONTEND_MODE=git \
COMFY_BUILD_TARGET=runtime-frontend-git-sage \
FRONTEND_GIT_URL=https://github.com/Comfy-Org/ComfyUI_frontend.git \
FRONTEND_GIT_REF=0000000000000000000000000000000000000000 \
FRONTEND_GIT_REQUESTED_REF=fixture \
  podman-compose "${compose_files[@]}" \
    --env-file versions/edge.env \
    --podman-path "$fake_engine" config >/dev/null

COMFY_FRONTEND_MODE=dist \
COMFY_FRONTEND_DIST_DIR="$PROJECT_ROOT/tests/fixtures/frontend-dist" \
  podman-compose "${compose_files[@]}" \
    --file compose.frontend-dist.yaml \
    --env-file versions/current.env \
    --podman-path "$fake_engine" config >/dev/null

tool_services=$(podman-compose "${compose_files[@]}" \
  --env-file versions/edge.env --profile tools \
  --podman-path "$fake_engine" config --services)
for service in \
  comfy \
  node-deps-snapshot \
  frontend-release-pin \
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
  frontend-release-pin \
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
  frontend-release-pin \
  node-set \
  node-set-status \
  frontend-fetch \
  frontend-build; do
  podman_compose_tool_dry_run run --rm --no-deps "$service"
done
podman_compose_tool_dry_run run --rm --no-deps -T frontend-release-pin \
  digest Comfy-Org/ComfyUI_frontend@v1.50.4
for service in model-set model-set-status; do
  podman_compose_tool_dry_run run --rm --no-deps -T "$service" status all
done
podman_compose_tool_dry_run run --rm --no-deps -T template-inspector list
podman_compose_tool_dry_run run --rm --no-deps -T template-draft \
  create-model-set fixture --name fixture

podman_compose_dry_run build comfy
podman_compose_dry_run up --no-build --detach comfy
podman_compose_dry_run up --no-build --force-recreate --detach comfy
podman_compose_dry_run down
podman_compose_dry_run ps
podman_compose_dry_run logs --follow comfy
podman_compose_dry_run exec comfy bash

printf 'LatentCrate podman-compose 1.6 compatibility checks passed.\n'
