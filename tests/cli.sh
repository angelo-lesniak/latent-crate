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
  COMFY_ALLOW_REMOTE \
  COMFY_BIND_ADDRESS \
  COMPOSE_PROJECT_NAME \
  CONTAINER_ENGINE \
  FRONTEND_OUTPUT_DIR \
  FRONTEND_PNPM_CACHE_DIR \
  FRONTEND_SOURCE_DIR \
  FRONTEND_WORK_DIR \
  HF_TOKEN \
  LATENTCRATE_HF_TOKEN_VALUE \
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
node_sets=$(bash bin/latentcrate nodes list)
[[ "$node_sets" == *latent-nodepack* ]]
model_sets=$(bash bin/latentcrate models list)
[[ "$model_sets" == *flux2-klein-9b-distilled* ]]
[[ "$model_sets" == *minimax-h3-r2v* ]]

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
[[ "$release_config" == *'target=runtime-sage mode=release'* ]]

release_no_sage_config=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate config current --no-sage --frontend-release)
[[ "$release_no_sage_config" == *'target=runtime mode=release'* ]]

release_env_no_sage_config=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  LATENTCRATE_SAGE=false bash bin/latentcrate config current --frontend-release)
[[ "$release_env_no_sage_config" == *'target=runtime mode=release'* ]]

if invalid_sage_output=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
    LATENTCRATE_SAGE=maybe bash bin/latentcrate config current --frontend-release 2>&1); then
  printf 'cli test: invalid LATENTCRATE_SAGE unexpectedly succeeded\n' >&2
  exit 1
fi
[[ "$invalid_sage_output" == *'LATENTCRATE_SAGE must be true or false'* ]]

git_config=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate config edge --frontend-git \
    https://github.com/Comfy-Org/ComfyUI_frontend.git \
    0000000000000000000000000000000000000000)
[[ "$git_config" == *'target=runtime-frontend-git-sage mode=git'* ]]

git_sage_config=$(PATH="$fake_bin:$PATH" CONTAINER_ENGINE=docker \
  bash bin/latentcrate config edge --sage --frontend-git \
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
