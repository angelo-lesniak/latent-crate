#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
readonly PROJECT_ROOT
cd "$PROJECT_ROOT"

unset \
  COMFY_FRONTEND_MODE \
  COMFY_FRONTEND_DIST_DIR \
  COMFY_FRONTEND_SOURCE_DIR \
  LATENTCRATE_SAGE \
  LATENTCRATE_SAGE_MODE \
  COMFY_ALLOW_REMOTE \
  COMFY_BIND_ADDRESS \
  COMFY_MODELS_DIR \
  COMPOSE_PROJECT_NAME \
  CONTAINER_ENGINE \
  FRONTEND_OUTPUT_DIR \
  FRONTEND_PNPM_CACHE_DIR \
  FRONTEND_SOURCE_DIR \
  FRONTEND_WORK_DIR \
  HF_TOKEN \
  LATENTCRATE_HF_TOKEN_VALUE \
  TEMPLATE_DRAFT_OUTPUT_DIR \
  FAKE_FRONTEND_PROFILE_TO_CHANGE \
  FAKE_FRONTEND_RELEASE_DIGEST \
  FAKE_DOCKER_TRACE_STDERR \
  FAKE_VERSION_PROFILE_TO_CHANGE \
  FAKE_VERSION_UPDATE_OUTPUT \
  FAKE_DOCKER_COMFY_RUNNING
fake_bin="$PROJECT_ROOT/tests/fixtures/fake-bin"
chmod +x "$fake_bin/docker" "$fake_bin/flock"

# The parser checks run engine-free on macOS and Windows dev machines, so
# bypass the wrapper's Linux-only platform guard.
export LATENTCRATE_SKIP_PLATFORM_CHECK=1

expect_failure() {
  local expected=$1
  shift
  local output

  if output=$(bash bin/latentcrate "$@" 2>&1); then
    printf 'cli test: command unexpectedly succeeded: %s\n' "$*" >&2
    exit 1
  fi
  if [[ "$output" != *"$expected"* ]]; then
    printf 'cli test: expected %q in output from: %s\n' "$expected" "$*" >&2
    printf '%s\n' "$output" >&2
    exit 1
  fi
}

bash bin/latentcrate --help >/dev/null
bash bin/latentcrate versions >/dev/null
edge_frontend_digest=$(awk -F= '$1 == "COMFY_FRONTEND_DIST_SHA256" {print $2}' versions/edge.env)
pin_frontend_release=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  FAKE_FRONTEND_RELEASE_DIGEST="$edge_frontend_digest" \
  bash bin/latentcrate frontend pin-release edge)
[[ "$pin_frontend_release" == *'build frontend-release-pin'* ]]
[[ "$pin_frontend_release" == *'Already pinned '*' for profile edge:'* ]]
version_update=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  FAKE_DOCKER_TRACE_STDERR=true \
  FAKE_VERSION_UPDATE_OUTPUT='LATENTCRATE_VERSION_RESULT|node|0' \
  bash bin/latentcrate versions update node edge 2>&1)
[[ "$version_update" == *'build version-update'* ]]
[[ "$version_update" == *'run --rm --no-deps -T version-update resolve node '* ]]
[[ "$version_update" == *'already has the latest eligible node versions.'* ]]
node_sets=$(bash bin/latentcrate nodes list)
[[ "$node_sets" == *latent-nodepack* ]]
model_sets=$(bash bin/latentcrate models list)
[[ "$model_sets" == *flux2-klein-9b-distilled* ]]
[[ "$model_sets" == *minimax-h3-r2v* ]]

template_list=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate templates list edge)
[[ "$template_list" == *'build comfy'* ]]
[[ "$template_list" == *'run --rm --no-deps -T template-inspector list'* ]]

template_draft=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate templates create-model-set video_minimax_h3_i2v edge \
    --name minimax-h3-i2v-draft)
[[ "$template_draft" == *'build comfy'* ]]
[[ "$template_draft" == *'template-draft create-model-set video_minimax_h3_i2v --name minimax-h3-i2v-draft'* ]]

mkdir -p "$PROJECT_ROOT/data/comfy/custom_nodes"
node_set_install=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate nodes install latent-nodepack current)
[[ "$node_set_install" == *'build node-set'* ]]

if running_node_output=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
    FAKE_DOCKER_COMFY_RUNNING=true \
    bash bin/latentcrate nodes install latent-nodepack current 2>&1); then
  printf 'cli test: node-set install unexpectedly changed a running ComfyUI service\n' >&2
  exit 1
fi
[[ "$running_node_output" == *'stop ComfyUI before installing or syncing'* ]]

status_output=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate status current)
[[ "$status_output" == *' ps'* ]]
[[ "$status_output" != *' ps comfy'* ]]

status_output=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  LATENTCRATE_HF_TOKEN_VALUE=caller-supplied-secret \
  bash bin/latentcrate status current)
[[ "$status_output" == *' ps'* ]]

mkdir -p "$PROJECT_ROOT/data/models"
model_status=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate models status --profile edge krea2-t2i-int8)
[[ "$model_status" == *'build model-set-status'* ]]
[[ "$model_status" == *'run --rm --no-deps -T model-set-status status krea2-t2i-int8'* ]]

model_fetch=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker HF_TOKEN=secret-fixture \
  bash bin/latentcrate models fetch flux2-klein-9b-distilled minimax-h3-r2v)
[[ "$model_fetch" == *'build model-set'* ]]
[[ "$model_fetch" == *'model-set fetch --token-stdin flux2-klein-9b-distilled minimax-h3-r2v'* ]]

expect_failure 'only valid with up, config, or smoke-gpu' build current --frontend-dist tests/fixtures/frontend-dist
expect_failure 'only valid with up, config, or smoke-gpu' build current --frontend-source tests/fixtures/frontend-source
expect_failure 'must use HTTPS' build current --frontend-git git@example.invalid:fork/frontend.git main
expect_failure 'must not begin with a dash' build current --frontend-git https://example.invalid/frontend.git --upload-pack=bad
expect_failure 'multiple version profiles' up current edge
expect_failure 'positive integer' wait current --timeout nope
expect_failure 'missing or unreadable' config current --frontend-dist tests/fixtures/missing-dist
expect_failure 'does not exist' config current --frontend-source tests/fixtures/missing-source
expect_failure 'only valid with up or build' config current --use-saved-node-deps
expect_failure 'unknown option for up: --refresh-node-deps' up current --refresh-node-deps
expect_failure 'only valid with up' build current --model-set krea2-t2i-int8
expect_failure 'all cannot be combined' models status all krea2-t2i-int8
expect_failure 'at most one version profile' templates list current edge
expect_failure 'at most one version profile' frontend pin-release current edge
expect_failure 'unknown version profile' frontend pin-release missing
expect_failure 'usage: bin/latentcrate versions update' versions update node
expect_failure 'unknown version component' versions update unknown edge
expect_failure 'unknown version profile' versions update node missing
expect_failure 'unsafe template name' templates create-model-set ../unsafe
expect_failure 'unsafe model-set name' templates create-model-set fixture --name all

if remote_output=$(COMFY_BIND_ADDRESS=0.0.0.0 \
    bash bin/latentcrate up current --detach 2>&1); then
  printf 'cli test: remote bind unexpectedly succeeded without acknowledgement\n' >&2
  exit 1
fi
[[ "$remote_output" == *'set COMFY_ALLOW_REMOTE=true'* ]]

remote_output=$(PATH="$fake_bin:$PATH" \
  CONTAINER_ENGINE=docker \
  COMFY_BIND_ADDRESS=0.0.0.0 \
  COMFY_ALLOW_REMOTE=true \
  bash bin/latentcrate up current --use-saved-node-deps --detach)
[[ "$remote_output" == *'target=runtime-sage mode=release'* ]]
[[ "$remote_output" == *'up --no-build --force-recreate --detach comfy'* ]]

release_config=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate config current --frontend-release)
[[ "$release_config" == *'target=runtime-sage mode=release sage=available'* ]]

release_no_sage_config=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate config current --sage off --frontend-release)
[[ "$release_no_sage_config" == *'target=runtime mode=release sage=off'* ]]

if removed_no_sage_output=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
    bash bin/latentcrate config current --no-sage --frontend-release 2>&1); then
  printf 'cli test: removed --no-sage flag unexpectedly succeeded\n' >&2
  exit 1
fi
[[ "$removed_no_sage_output" == *'unknown option for config: --no-sage'* ]]

release_env_no_sage_config=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  LATENTCRATE_SAGE=off bash bin/latentcrate config current --frontend-release)
[[ "$release_env_no_sage_config" == *'target=runtime mode=release'* ]]

release_global_config=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate config current --sage global --frontend-release)
[[ "$release_global_config" == *'target=runtime-sage mode=release sage=global'* ]]

release_global_env_config=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  LATENTCRATE_SAGE=global bash bin/latentcrate config current --frontend-release)
[[ "$release_global_env_config" == *'target=runtime-sage mode=release sage=global'* ]]

if trailing_sage_output=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
    bash bin/latentcrate config current --frontend-release --sage 2>&1); then
  printf 'cli test: trailing --sage without a mode unexpectedly succeeded\n' >&2
  exit 1
fi
[[ "$trailing_sage_output" == *'--sage requires a mode: off, available, or global'* ]]

if invalid_sage_output=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
    LATENTCRATE_SAGE=maybe bash bin/latentcrate config current --frontend-release 2>&1); then
  printf 'cli test: invalid LATENTCRATE_SAGE unexpectedly succeeded\n' >&2
  exit 1
fi
[[ "$invalid_sage_output" == *'LATENTCRATE_SAGE must be off, available, or global'* ]]

if bad_sage_mode_output=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
    bash bin/latentcrate config current --sage maybe --frontend-release 2>&1); then
  printf 'cli test: invalid --sage mode unexpectedly succeeded\n' >&2
  exit 1
fi
[[ "$bad_sage_mode_output" == *'--sage requires a mode: off, available, or global'* ]]

git_config=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate config edge --frontend-git \
    https://github.com/Comfy-Org/ComfyUI_frontend.git \
    0000000000000000000000000000000000000000)
[[ "$git_config" == *'target=runtime-frontend-git-sage mode=git'* ]]

git_sage_config=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate config edge --sage available --frontend-git \
    https://github.com/Comfy-Org/ComfyUI_frontend.git \
    0000000000000000000000000000000000000000)
[[ "$git_sage_config" == *'target=runtime-frontend-git-sage mode=git'* ]]

local_config=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate config current --frontend-dist tests/fixtures/frontend-dist)
[[ "$local_config" == *'target=runtime-sage mode=dist'* ]]
[[ "$local_config" == *'compose.frontend-dist.yaml'* ]]

source_config=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate config current --frontend-source tests/fixtures/frontend-source)
[[ "$source_config" == *'target=runtime-sage mode=dist'* ]]
[[ "$source_config" == *'compose.frontend-dist.yaml'* ]]

local_up=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate up current --use-saved-node-deps \
    --frontend-dist tests/fixtures/frontend-dist --detach)
[[ "$local_up" == *'--force-recreate'* ]]

model_up=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate up current --use-saved-node-deps \
    --model-set krea2-t2i-int8 --model-set krea2-style-reference-int8 --detach)
[[ "$model_up" == *'model-set fetch --token-stdin krea2-t2i-int8 krea2-style-reference-int8'* ]]
[[ "$model_up" == *'up --no-build --force-recreate --detach comfy'* ]]

printf 'LatentCrate CLI parser checks passed.\n'
