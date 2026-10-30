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
  FAKE_DOCKER_TRACE_STDERR \
  FAKE_DOCKER_COMFY_RUNNING
fake_bin="$PROJECT_ROOT/tests/fixtures/fake-bin"
doctor_bin="$PROJECT_ROOT/tests/fixtures/doctor-bin"
resolver_bin="$PROJECT_ROOT/tests/fixtures/resolver-bin"
# Git stores these fixtures without executable bits; enable them before PATH use.
chmod +x "$fake_bin/docker" "$fake_bin/flock" "$doctor_bin"/* "$resolver_bin/git"

# The parser checks run engine-free on macOS and Windows dev machines, so
# bypass the wrapper's Linux-only platform guard.
export LATENTCRATE_SKIP_PLATFORM_CHECK=1
# A user's ignored .env may set another Sage default. Keep the default
# routing tests hermetic; tests/check-project.py asserts the code fallback.
export LATENTCRATE_SAGE=off
export COMFY_DATA_DIR="$PROJECT_ROOT/data/comfy"
export COMFY_MODELS_DIR="$PROJECT_ROOT/data/models"
export COMFY_CACHE_DIR="$PROJECT_ROOT/data/cache"
export COMFY_LOCAL_NODES_DIR="$PROJECT_ROOT/local/custom_nodes"
mkdir -p \
  "$COMFY_DATA_DIR/custom_nodes" \
  "$COMFY_MODELS_DIR" \
  "$COMFY_CACHE_DIR" \
  "$COMFY_LOCAL_NODES_DIR"

# The default build resolves the pinned frontend tag through git ls-remote;
# the fake git answers for that tag offline.
export FAKE_GIT_MODE=annotated-tag
FAKE_GIT_REF=$(bash bin/latentcrate pins | awk -F= '$1 == "COMFYUI_FRONTEND_REF" {print $2}')
export FAKE_GIT_REF
[[ -n "$FAKE_GIT_REF" ]] || { printf 'cli test: pins misses COMFYUI_FRONTEND_REF\n' >&2; exit 1; }
PATH="$fake_bin:$resolver_bin:$PATH"
export PATH

# `up` copies the requirement tree the snapshot helper publishes; the fake
# engine publishes nothing, so provide an empty manifest for the duration.
requirements_manifest="$PROJECT_ROOT/build/custom-node-requirements/manifest.txt"
created_manifest=false
if [[ ! -e "$requirements_manifest" ]]; then
  mkdir -p "${requirements_manifest%/*}"
  : > "$requirements_manifest"
  created_manifest=true
fi
cleanup() {
  [[ "$created_manifest" != true ]] || rm -f -- "$requirements_manifest"
}
trap cleanup EXIT

expect_failure() {
  local expected=$1
  shift
  local output

  if output=$(CONTAINER_ENGINE=docker bash bin/latentcrate "$@" 2>&1); then
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
pins=$(bash bin/latentcrate pins)
[[ "$pins" == *"COMFYUI_FRONTEND_REF=$FAKE_GIT_REF"* ]]
doctor_default=$(PATH="$doctor_bin:$PATH" \
  CONTAINER_ENGINE=podman bash bin/latentcrate doctor 2>&1) || {
  printf 'cli test: default doctor failed:\n%s\n' "$doctor_default" >&2
  exit 1
}
[[ "$doctor_default" == *'checking the smaller image without SageAttention'* ]]
[[ "$doctor_default" == *'podman version 5.2.0-fake'* ]]

doctor_sage=$(PATH="$doctor_bin:$PATH" \
  CONTAINER_ENGINE=podman bash bin/latentcrate doctor --sage available 2>&1) || {
  printf 'cli test: Sage doctor failed:\n%s\n' "$doctor_sage" >&2
  exit 1
}
[[ "$doctor_sage" == *'checking the SageAttention-capable image'* ]]

node_sets=$(bash bin/latentcrate nodes list)
[[ "$node_sets" == *latent-nodepack* ]]
model_sets=$(bash bin/latentcrate models list)
[[ "$model_sets" == *flux2-klein-9b-distilled* ]]
[[ "$model_sets" == *minimax-h3-r2v* ]]
[[ "$model_sets" == *minimax-h3-i2v-turbo-4step-v1-1* ]]
[[ "$model_sets" == *minimax-h3-prompt-embeddings* ]]

template_list=$(CONTAINER_ENGINE=docker bash bin/latentcrate templates list)
[[ "$template_list" == *'build comfy'* ]]
[[ "$template_list" == *'target=runtime mode=release sage=off'* ]]
[[ "$template_list" == *'run --rm --no-deps -T template-inspector list'* ]]

template_draft=$(CONTAINER_ENGINE=docker \
  bash bin/latentcrate templates create-model-set video_minimax_h3_i2v \
    --name minimax-h3-i2v-draft)
[[ "$template_draft" == *'build comfy'* ]]
[[ "$template_draft" == *'template-draft create-model-set video_minimax_h3_i2v --name minimax-h3-i2v-draft'* ]]

node_set_install=$(CONTAINER_ENGINE=docker \
  bash bin/latentcrate nodes install latent-nodepack)
[[ "$node_set_install" == *'build node-set'* ]]

if running_node_output=$(CONTAINER_ENGINE=docker \
    FAKE_DOCKER_COMFY_RUNNING=true \
    bash bin/latentcrate nodes install latent-nodepack 2>&1); then
  printf 'cli test: node-set install unexpectedly changed a running ComfyUI service\n' >&2
  exit 1
fi
[[ "$running_node_output" == *'stop ComfyUI before installing or syncing'* ]]

status_output=$(CONTAINER_ENGINE=docker bash bin/latentcrate status)
[[ "$status_output" == *' ps'* ]]
[[ "$status_output" != *' ps comfy'* ]]
[[ "$status_output" == *'compose.gpu.yaml'* ]]

status_output=$(CONTAINER_ENGINE=docker \
  LATENTCRATE_HF_TOKEN_VALUE=caller-supplied-secret \
  bash bin/latentcrate status)
[[ "$status_output" == *' ps'* ]]

model_status=$(CONTAINER_ENGINE=docker \
  bash bin/latentcrate models status krea2-t2i-int8)
[[ "$model_status" == *'build model-set-status'* ]]
[[ "$model_status" == *'run --rm --no-deps -T model-set-status status krea2-t2i-int8'* ]]

model_fetch=$(CONTAINER_ENGINE=docker HF_TOKEN=secret-fixture \
  bash bin/latentcrate models fetch flux2-klein-9b-distilled minimax-h3-r2v)
[[ "$model_fetch" == *'build model-set'* ]]
[[ "$model_fetch" == *'model-set fetch --token-stdin flux2-klein-9b-distilled minimax-h3-r2v'* ]]

expect_failure 'only valid with up, config, or smoke-gpu' build --frontend-dist tests/fixtures/frontend-dist
expect_failure 'only valid with up, config, or smoke-gpu' build --frontend-source tests/fixtures/frontend-source
expect_failure 'must use HTTPS' build --frontend-git git@example.invalid:fork/frontend.git main
expect_failure 'must not begin with a dash' build --frontend-git https://example.invalid/frontend.git --upload-pack=bad
expect_failure 'unknown argument for up: current' up current
expect_failure 'positive integer' wait --timeout nope
expect_failure 'missing or unreadable' config --frontend-dist tests/fixtures/missing-dist
expect_failure 'does not exist' config --frontend-source tests/fixtures/missing-source
expect_failure 'unknown option for up: --refresh-node-deps' up --refresh-node-deps
expect_failure 'only valid with up' build --model-set krea2-t2i-int8
expect_failure 'all cannot be combined' models status all krea2-t2i-int8
expect_failure 'templates list accepts no additional arguments' templates list current
expect_failure 'unsafe template name' templates create-model-set ../unsafe
expect_failure 'unsafe model-set name' templates create-model-set fixture --name all

if localhost_output=$(CONTAINER_ENGINE=docker COMFY_BIND_ADDRESS=localhost \
    bash bin/latentcrate up --detach 2>&1); then
  printf 'cli test: COMFY_BIND_ADDRESS=localhost unexpectedly succeeded\n' >&2
  exit 1
fi
[[ "$localhost_output" == *'use 127.0.0.1 or [::1]'* ]]

if remote_output=$(CONTAINER_ENGINE=docker COMFY_BIND_ADDRESS=0.0.0.0 \
    bash bin/latentcrate up --detach 2>&1); then
  printf 'cli test: remote bind unexpectedly succeeded without acknowledgement\n' >&2
  exit 1
fi
[[ "$remote_output" == *'set COMFY_ALLOW_REMOTE=true'* ]]

remote_output=$(CONTAINER_ENGINE=docker \
  COMFY_BIND_ADDRESS=0.0.0.0 \
  COMFY_ALLOW_REMOTE=true \
  bash bin/latentcrate up --detach)
[[ "$remote_output" == *'target=runtime mode=release'* ]]
[[ "$remote_output" == *'up --no-build --force-recreate --detach comfy'* ]]
[[ "$remote_output" == *'FRONTEND_GIT_URL=https://github.com/Comfy-Org/ComfyUI_frontend FRONTEND_GIT_REF=3333333333333333333333333333333333333333 FRONTEND_GIT_REQUESTED_REF=v1.53.1'* ]]

release_config=$(CONTAINER_ENGINE=docker bash bin/latentcrate config)
[[ "$release_config" == *'target=runtime mode=release sage=off image=latentcrate/comfy:release-'* ]]
[[ "$release_config" == *'--env-file pins.env'* ]]
[[ "$release_config" == *'FRONTEND_GIT_URL=https://github.com/Comfy-Org/ComfyUI_frontend FRONTEND_GIT_REF=3333333333333333333333333333333333333333 FRONTEND_GIT_REQUESTED_REF=v1.53.1'* ]]
[[ "$release_config" != *'compose.gpu.yaml'* ]]

release_sage_config=$(CONTAINER_ENGINE=docker \
  bash bin/latentcrate config --sage available)
[[ "$release_sage_config" == *'target=runtime-sage mode=release sage=available'* ]]

release_persistent_sage_override=$(CONTAINER_ENGINE=docker \
  LATENTCRATE_SAGE=available \
  bash bin/latentcrate config --sage off)
[[ "$release_persistent_sage_override" == *'target=runtime mode=release sage=off'* ]]

expect_failure 'unknown option for config: --no-sage' config --no-sage

release_env_sage_config=$(CONTAINER_ENGINE=docker \
  LATENTCRATE_SAGE=available bash bin/latentcrate config)
[[ "$release_env_sage_config" == *'target=runtime-sage mode=release sage=available'* ]]

release_global_config=$(CONTAINER_ENGINE=docker \
  bash bin/latentcrate config --sage global)
[[ "$release_global_config" == *'target=runtime-sage mode=release sage=global'* ]]

release_global_env_config=$(CONTAINER_ENGINE=docker \
  LATENTCRATE_SAGE=global bash bin/latentcrate config)
[[ "$release_global_env_config" == *'target=runtime-sage mode=release sage=global'* ]]

expect_failure '--sage requires a mode: off, available, or global' config --sage
expect_failure '--sage requires a mode: off, available, or global' config --sage maybe

if invalid_sage_output=$(CONTAINER_ENGINE=docker \
    LATENTCRATE_SAGE=maybe bash bin/latentcrate config 2>&1); then
  printf 'cli test: invalid LATENTCRATE_SAGE unexpectedly succeeded\n' >&2
  exit 1
fi
[[ "$invalid_sage_output" == *'LATENTCRATE_SAGE must be off, available, or global'* ]]

git_config=$(CONTAINER_ENGINE=docker \
  bash bin/latentcrate config --frontend-git \
    https://github.com/Comfy-Org/ComfyUI_frontend.git \
    0000000000000000000000000000000000000000)
[[ "$git_config" == *'target=runtime mode=git'* ]]
[[ "$git_config" == *'FRONTEND_GIT_URL=https://github.com/Comfy-Org/ComfyUI_frontend.git FRONTEND_GIT_REF=0000000000000000000000000000000000000000 FRONTEND_GIT_REQUESTED_REF=0000000000000000000000000000000000000000'* ]]

git_sage_config=$(CONTAINER_ENGINE=docker \
  bash bin/latentcrate config --sage available --frontend-git \
    https://github.com/Comfy-Org/ComfyUI_frontend.git \
    0000000000000000000000000000000000000000)
[[ "$git_sage_config" == *'target=runtime-sage mode=git'* ]]

local_config=$(CONTAINER_ENGINE=docker \
  bash bin/latentcrate config --frontend-dist tests/fixtures/frontend-dist)
[[ "$local_config" == *'target=comfy-runtime mode=dist'* ]]
[[ "$local_config" == *'compose.frontend-dist.yaml'* ]]
[[ "$local_config" == *'FRONTEND_GIT_URL= FRONTEND_GIT_REF= FRONTEND_GIT_REQUESTED_REF='* ]]

local_sage_config=$(CONTAINER_ENGINE=docker \
  bash bin/latentcrate config --sage available \
    --frontend-dist tests/fixtures/frontend-dist)
[[ "$local_sage_config" == *'target=runtime-sage mode=dist sage=available'* ]]

source_config=$(CONTAINER_ENGINE=docker \
  bash bin/latentcrate config --frontend-source tests/fixtures/frontend-source)
[[ "$source_config" == *'target=comfy-runtime mode=dist'* ]]
[[ "$source_config" == *'compose.frontend-dist.yaml'* ]]
[[ "$source_config" == *'FRONTEND_GIT_URL= FRONTEND_GIT_REF= FRONTEND_GIT_REQUESTED_REF='* ]]

source_sage_config=$(CONTAINER_ENGINE=docker \
  bash bin/latentcrate config --sage available \
    --frontend-source tests/fixtures/frontend-source)
[[ "$source_sage_config" == *'target=runtime-sage mode=dist sage=available'* ]]

local_up=$(CONTAINER_ENGINE=docker \
  bash bin/latentcrate up --frontend-dist tests/fixtures/frontend-dist --detach)
[[ "$local_up" == *'--force-recreate'* ]]
[[ "$local_up" == *'compose.gpu.yaml'* ]]

local_smoke=$(CONTAINER_ENGINE=docker FAKE_DOCKER_COMFY_RUNNING=true \
  bash bin/latentcrate smoke-gpu --frontend-dist tests/fixtures/frontend-dist)
[[ "$local_smoke" == *'compose.frontend-dist.yaml'*' exec comfy '* ]]
rm -f -- "${local_smoke##*Saved GPU report to }"

model_up=$(CONTAINER_ENGINE=docker \
  bash bin/latentcrate up \
    --model-set krea2-t2i-int8 --model-set krea2-style-reference-int8 --detach)
[[ "$model_up" == *'model-set fetch --token-stdin krea2-t2i-int8 krea2-style-reference-int8'* ]]
[[ "$model_up" == *'up --no-build --force-recreate --detach comfy'* ]]

printf 'LatentCrate CLI parser checks passed.\n'
