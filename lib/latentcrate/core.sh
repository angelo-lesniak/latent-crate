# shellcheck shell=bash

[[ -n "${PROJECT_ROOT:-}" ]] \
  || { printf 'LatentCrate: PROJECT_ROOT must be set before loading core.sh\n' >&2; return 1; }

die() {
  printf 'LatentCrate: %s\n' "$*" >&2
  exit 1
}

is_true() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

load_local_env() {
  local name
  local -A existing_environment=()
  [[ ! -L "$PROJECT_ROOT/.env" ]] \
    || die "refusing symbolic link for local settings: $PROJECT_ROOT/.env"
  while IFS= read -r name; do
    existing_environment["$name"]=${!name}
  done < <(compgen -e)
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env.example"
  if [[ -f "$PROJECT_ROOT/.env" ]]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.env"
  fi
  set +a
  for name in "${!existing_environment[@]}"; do
    # Read-only variables (for example an exported SHELLOPTS) cannot be
    # reassigned; printf -v would abort the wrapper under set -e.
    if ! (unset "$name" 2>/dev/null); then
      [[ "${!name-}" == "${existing_environment[$name]}" ]] \
        || printf 'LatentCrate: keeping read-only environment variable %s; the .env value is ignored\n' "$name" >&2
      continue
    fi
    printf -v "$name" '%s' "${existing_environment[$name]}"
    # ${name?} is ShellCheck's documented quiet form for a dynamic export.
    export "${name?}"
  done
  export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-latentcrate}"
  HOST_UID=$(id -u) || die 'could not determine the calling user with id -u'
  HOST_GID=$(id -g) || die 'could not determine the calling group with id -g'
  export HOST_UID HOST_GID
  if [[ "${COMFY_BIND_ADDRESS:-}" == ::1 ]]; then
    export COMFY_BIND_ADDRESS='[::1]'
  fi
}

# pins.env: plain KEY=VALUE lines, every key once, bare values. Every key is
# exported for the Compose build arguments; PIN_KEYS keeps the file order.
load_pins() {
  local path="$PROJECT_ROOT/pins.env" line key value lineno=0
  local key_re='^([A-Z][A-Z0-9_]*)=([A-Za-z0-9][A-Za-z0-9._+-]*)$'
  local -A seen=()

  [[ -f "$path" ]] || die "pins file is missing: $path"
  PIN_KEYS=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    ((++lineno))
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    [[ "$line" =~ $key_re ]] \
      || die "pins.env line $lineno must be KEY=VALUE with a bare value: $line"
    key=${BASH_REMATCH[1]}
    value=${BASH_REMATCH[2]}
    [[ -z "${seen[$key]:-}" ]] || die "pins.env defines $key twice (line $lineno)"
    seen[$key]=1
    PIN_KEYS+=("$key")
    printf -v "$key" '%s' "$value"
    export "${key?}"
  done < "$path"
}

print_pins() {
  local key
  for key in "${PIN_KEYS[@]}"; do
    printf '%s=%s\n' "$key" "${!key}"
  done
}

running_under_wsl() {
  grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null
}

# Under WSL, a Windows-side engine client (docker.exe/podman.exe on the
# interop PATH) must not be used in place of a Linux client.
windows_engine_executable() {
  local executable=$1
  running_under_wsl || return 1
  case "${executable,,}" in
    *.exe) return 0 ;;
  esac
  return 1
}

compose_engine_usable() {
  local engine=$1
  local executable
  executable=$(command -v "$engine" 2>/dev/null) || return 1
  if windows_engine_executable "$executable"; then
    return 1
  fi
  "$engine" info >/dev/null 2>&1 \
    && "$engine" compose version >/dev/null 2>&1
}

podman_supported() {
  local rootless runtime

  compose_engine_usable podman || return 1
  rootless=$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null) || return 1
  runtime=$(podman info --format '{{.Host.OCIRuntime.Name}}' 2>/dev/null) || return 1
  [[ "$rootless" == true && "$runtime" == crun ]]
}

engine_usable() {
  local engine=$1
  if [[ "$engine" == podman ]]; then
    podman_supported
  else
    compose_engine_usable "$engine"
  fi
}

detect_engine() {
  local candidate

  if [[ -n "${CONTAINER_ENGINE:-}" ]]; then
    candidate=$CONTAINER_ENGINE
    case "$candidate" in
      docker|podman) ;;
      *) die "unsupported CONTAINER_ENGINE=$candidate" ;;
    esac
    if [[ "$candidate" == podman ]]; then
      podman_supported \
        || die 'Podman requires a reachable rootless engine, crun, and a working Compose provider'
    else
      engine_usable "$candidate" \
        || die "$candidate requires a reachable engine and a working Compose provider"
    fi
  elif engine_usable podman; then
    candidate=podman
  elif engine_usable docker; then
    candidate=docker
  else
    die 'neither Podman nor Docker has both a reachable engine and Compose provider; run bin/latentcrate doctor'
  fi

  printf '%s\n' "$candidate"
}

prepare_host_directories() {
  local data_dir="${COMFY_DATA_DIR:-$PROJECT_ROOT/data/comfy}"
  local local_nodes_dir="${COMFY_LOCAL_NODES_DIR:-$PROJECT_ROOT/local/custom_nodes}"

  mkdir -p \
    "$data_dir/custom_nodes" \
    "${COMFY_MODELS_DIR:-$PROJECT_ROOT/data/models}" \
    "${COMFY_CACHE_DIR:-$PROJECT_ROOT/data/cache}" \
    "$local_nodes_dir"
  validate_custom_node_roots
}

validate_custom_node_roots() {
  local managed="${COMFY_DATA_DIR:-$PROJECT_ROOT/data/comfy}/custom_nodes"
  local local_nodes="${COMFY_LOCAL_NODES_DIR:-$PROJECT_ROOT/local/custom_nodes}"

  [[ -d "$managed" && -d "$local_nodes" ]] || return
  managed=$(cd -- "$managed" && pwd -P)
  local_nodes=$(cd -- "$local_nodes" && pwd -P)
  case "$managed/" in
    "$local_nodes/"*) die 'managed and local custom-node directories must not overlap' ;;
  esac
  case "$local_nodes/" in
    "$managed/"*) die 'managed and local custom-node directories must not overlap' ;;
  esac
}

validate_bind_address() {
  local address=${COMFY_BIND_ADDRESS:-127.0.0.1}

  case "$address" in
    127.0.0.1|'[::1]') return ;;
    localhost) die 'COMFY_BIND_ADDRESS=localhost is not a binding Compose accepts; use 127.0.0.1 or [::1]' ;;
  esac

  is_true "${COMFY_ALLOW_REMOTE:-false}" \
    || die "COMFY_BIND_ADDRESS=$address is not loopback; set COMFY_ALLOW_REMOTE=true only after adding authentication and TLS"
}

# Ask the container engine directly instead of relying on provider-specific
# `compose ps` syntax. Docker Compose and podman-compose both apply these
# standard Compose labels to service containers.
running_service_container_ids() {
  local engine=$1
  local service=$2
  local project=${COMPOSE_PROJECT_NAME:-latentcrate}

  [[ "$service" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] \
    || die "unsafe Compose service name: $service"
  "$engine" ps --quiet \
    --filter "label=com.docker.compose.project=$project" \
    --filter "label=com.docker.compose.service=$service"
}

compose() {
  local engine=$1
  shift
  local files=(
    --file compose.yaml
    --file "compose.${engine}.yaml"
  )

  if [[ "${INCLUDE_GPU_OVERLAY:-false}" == true ]]; then
    files+=(--file compose.gpu.yaml)
  fi
  if [[ "${INCLUDE_FRONTEND_OVERLAY:-false}" == true ]]; then
    files+=(--file compose.frontend-dist.yaml)
  fi

  (
    cd "$PROJECT_ROOT" || exit 1
    if [[ "$engine" == podman ]]; then
      # Podman's OCI output cannot retain Dockerfile SHELL metadata. Preserve
      # the Bash/pipefail build semantics for both podman-compose and direct
      # Podman-backed Compose providers.
      export BUILDAH_FORMAT=docker
    fi
    "$engine" compose \
      "${files[@]}" \
      --env-file pins.env \
      "$@"
  )
}

# Helper services stay behind the `tools` profile so a normal `compose up`
# starts only ComfyUI. Enable the profile explicitly because some Compose
# providers do not auto-enable it when a helper service is targeted by name.
compose_tool() {
  local engine=$1
  shift
  compose "$engine" --profile tools "$@"
}

init_project() {
  if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
    cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
    sed -i.bak -e "s/^HOST_MODEL_GID=.*/HOST_MODEL_GID=$(id -g)/" "$PROJECT_ROOT/.env"
    rm -f "$PROJECT_ROOT/.env.bak"
    printf 'Created %s\n' "$PROJECT_ROOT/.env"
  else
    printf 'Keeping existing %s\n' "$PROJECT_ROOT/.env"
  fi

  chmod 0600 "$PROJECT_ROOT/.env" \
    || die "could not restrict access to $PROJECT_ROOT/.env"

  load_local_env
  prepare_host_directories
  printf 'LatentCrate host directories are ready. Review .env before building.\n'
}
