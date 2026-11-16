#!/usr/bin/env bash
# Renders each Compose file combination the package can pass with the pinned
# standalone podman-compose parser and checks the container arguments the
# containment and privacy model depends on. No container engine is used: the
# fake podman only answers the probes that podman-compose performs.
set -Eeuo pipefail

# Physical, so the bind sources podman-compose resolves match the logged ones.
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
readonly PROJECT_ROOT
cd "$PROJECT_ROOT"

command -v podman-compose >/dev/null 2>&1 \
  || { printf 'podman-compose compatibility: podman-compose is required\n' >&2; exit 1; }

unset FAKE_PODMAN_LOG HF_TOKEN CIVITAI_TOKEN
# The Compose files require every setting and the values the package
# derives on each call (compose.yaml, the interpolation note). The settings
# come from the reference at their documented defaults; the storage paths
# are then pointed at a scratch directory.
set -a
# shellcheck source=/dev/null
. ./.env.example
set +a
HOST_UID=$(id -u)
HOST_GID=$(id -g)
export HOST_UID HOST_GID HOST_MODEL_GID="$HOST_GID"
export FRONTEND_GIT_URL=https://github.com/Comfy-Org/ComfyUI_frontend.git
export FRONTEND_GIT_REF=0000000000000000000000000000000000000000
export FRONTEND_GIT_REQUESTED_REF=fixture
TEST_ROOT=$(mktemp -d)
# podman-compose creates a missing bind source, and the private workflow
# directory's lies in the checkout; the levels this run creates are removed.
created=()
for level in local local/workflow-sets local/workflow-sets/workflows; do
  [[ -e "$level" ]] || created=("$level" "${created[@]}")
done
cleanup() {
  rm -rf -- "$TEST_ROOT"
  local level
  for level in "${created[@]}"; do
    [[ ! -d "$level" ]] || rmdir -- "$level"
  done
}
trap cleanup EXIT
export COMFY_DATA_DIR="$TEST_ROOT/data"
export COMFY_MODELS_DIR="$TEST_ROOT/models"
export COMFY_CACHE_DIR="$TEST_ROOT/cache"
export COMFY_LOCAL_NODES_DIR="$TEST_ROOT/local-nodes"
dist_dir="$PROJECT_ROOT/tests/fixtures/frontend-dist"
run_log="$TEST_ROOT/run.log"

fake_engine="$PROJECT_ROOT/tests/fixtures/podman-compose-bin/podman"
chmod +x "$fake_engine"

# podman-compose warns about every network a render leaves unused; its
# output is shown only when a render fails.
quiet() {
  local output
  output=$("$@" 2>&1 >/dev/null) || { printf '%s\n' "$output" >&2; return 1; }
}

fail() {
  printf 'podman-compose compatibility: %s\n' "$1" >&2
  exit 1
}

# The variants (variants.Variant.compose_env): the release and Git
# frontends run the `runtime` target as container mode git, dist mode the
# frontend-less `comfy-runtime` with the dist overlay, and a Sage mode the
# `runtime-sage` target, with the dist overlay on top when both are set.
# Each runs the command that follows.
release() {
  LATENTCRATE_IMAGE=latentcrate/comfy:release-fixture COMFY_BUILD_TARGET=runtime COMFY_FRONTEND_MODE=git "$@"
}
sage() {
  LATENTCRATE_IMAGE=latentcrate/comfy:release-sage-fixture COMFY_BUILD_TARGET=runtime-sage COMFY_FRONTEND_MODE=git \
    LATENTCRATE_SAGE=available "$@"
}
dist() {
  LATENTCRATE_IMAGE=latentcrate/comfy:dist-fixture COMFY_BUILD_TARGET=comfy-runtime COMFY_FRONTEND_MODE=dist \
    COMFY_FRONTEND_DIST_DIR="$dist_dir" "$@"
}
dist_sage() {
  LATENTCRATE_IMAGE=latentcrate/comfy:dist-sage-fixture COMFY_BUILD_TARGET=runtime-sage COMFY_FRONTEND_MODE=dist \
    COMFY_FRONTEND_DIST_DIR="$dist_dir" LATENTCRATE_SAGE=available "$@"
}
# The file combinations engine.Engine.compose passes: the service with the
# Podman overlay, the GPU device request unless the command runs without a
# GPU, and the dist-mode frontend mount (added by the caller).
base() {
  podman-compose --file compose.yaml --file compose.podman.yaml --env-file pins.env --podman-path "$fake_engine" "$@"
}
gpu() {
  base --file compose.gpu.yaml "$@"
}

# render <variant> <files> [more files]
render() {
  quiet "$1" "$2" "${@:3}" config
}

render release gpu
render sage gpu
render dist gpu --file compose.frontend-dist.yaml
render dist_sage gpu --file compose.frontend-dist.yaml
render release base

# The arguments a start produces, from the one `run` the fake engine logs;
# `--service-ports` publishes the port the way `up` does.
run_logged() {
  rm -f -- "$run_log"
  FAKE_PODMAN_LOG=$run_log quiet "$1" "$2" "${@:3}" run --rm --no-deps -T --service-ports comfy
}
logged() {
  grep -Fq -- "$1" "$run_log"
}

# A token in the environment reaches no Compose call (test_project_contracts
# pins the files; this pins the rendered arguments).
HF_TOKEN=hf-fixture-not-a-token CIVITAI_TOKEN=civitai-fixture-not-a-token run_logged release gpu
for leaked in hf-fixture-not-a-token HF_TOKEN civitai-fixture-not-a-token CIVITAI_TOKEN; do
  if logged "$leaked"; then
    fail 'a token reached the podman command line'
  fi
done
logged "--device $GPU_DEVICE" || fail 'comfy has no GPU device with the gpu overlay'
logged "-p $COMFY_BIND_ADDRESS:$COMFY_PORT:8188" || fail 'comfy does not publish the UI port on the bind address'
logged '--network=latentcrate_default:alias=comfy' || fail 'comfy does not use the default network'
logged '--read-only' || fail 'comfy is not read-only'
logged "keep-id:uid=$HOST_UID\\,gid=$HOST_GID" || fail 'comfy does not use keep-id'
logged '--group-add keep-groups' || fail 'comfy does not keep the supplementary groups'
logged '--label com.docker.compose.project=latentcrate' || fail 'the project label the package finds containers by is missing'
logged '--label com.docker.compose.service=comfy' || fail 'the service label the package finds containers by is missing'
logged "-v $COMFY_DATA_DIR:/data" || fail 'the data directory is not mounted writable at /data'
logged "-v $COMFY_MODELS_DIR:/models" || fail 'the models directory is not mounted writable at /models'
logged "-v $COMFY_CACHE_DIR:/cache" || fail 'the cache directory is not mounted writable at /cache'
logged "-v $COMFY_LOCAL_NODES_DIR:/local/custom_nodes:ro" || fail 'the local nodes are not mounted read-only'
for workflows in config/workflow-sets/workflows:latentcrate local/workflow-sets/workflows:latentcrate-local; do
  logged "-v $PROJECT_ROOT/${workflows%%:*}:/data/user/default/workflows/${workflows#*:}:ro" \
    || fail "the workflow sets in ${workflows%%:*} are not mounted read-only"
done
logged "-e LATENTCRATE_SAGE_MODE=$LATENTCRATE_SAGE" || fail 'the Sage mode does not reach the container'

# Without the GPU overlay (`templates`, --allow-no-gpu) no device is requested.
run_logged release base
if logged '--device'; then
  fail 'comfy has a GPU device without the gpu overlay'
fi

# Dist mode: the prebuilt tree is bound read-only at the entrypoint's root.
run_logged dist gpu --file compose.frontend-dist.yaml
logged "-v $dist_dir:/opt/latentcrate-frontend-dist:ro" || fail 'the dist overlay does not bind the frontend read-only'
logged '-e COMFY_FRONTEND_MODE=dist' || fail 'the dist overlay does not select dist mode in the container'

# Dist mode with Sage: the overlay and the Sage mode reach the same container.
run_logged dist_sage gpu --file compose.frontend-dist.yaml
logged "-v $dist_dir:/opt/latentcrate-frontend-dist:ro" || fail 'the dist overlay does not bind the frontend with Sage'
logged '-e LATENTCRATE_SAGE_MODE=available' || fail 'the Sage mode does not reach the dist container'

# The commands the package issues through Compose (runtime.py, templates.py).
dry_run() {
  quiet "$1" "$2" --dry-run "${@:3}"
}
dry_run release gpu build comfy
dry_run release gpu up --no-build --force-recreate --detach comfy
dry_run release base run --rm --no-deps -T --entrypoint python comfy \
  /usr/local/bin/latentcrate-manage-templates.py list
dry_run release base run --rm --no-deps -T \
  --volume "$TEST_ROOT/model-set-drafts:/output" --entrypoint python comfy \
  /usr/local/bin/latentcrate-manage-templates.py create-model-set fixture --name fixture

printf 'LatentCrate podman-compose 1.6 compatibility checks passed.\n'
