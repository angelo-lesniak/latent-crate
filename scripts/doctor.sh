#!/usr/bin/env bash
set -uo pipefail

PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
readonly PROJECT_ROOT
# Reuse the wrapper's engine/WSL/rootless-Podman detection instead of
# duplicating it here.
# shellcheck source=lib/latentcrate/core.sh
source "$PROJECT_ROOT/lib/latentcrate/core.sh"

requested_engine=
allow_no_gpu=false
minimum_driver_major=580
custom_node_architectures=
sage_architectures=
sage_enabled=false
failures=0
warnings=0
selected_engine=

usage_error() {
  printf 'doctor.sh: %s\n' "$1" >&2
  printf 'usage: doctor.sh [--engine docker|podman] [--allow-no-gpu true|false] [--min-driver-major n] [--custom-node-arch-list list] [--sage-arch-list list] [--sage true|false]\n' >&2
  exit 2
}

while (($# > 0)); do
  case "$1" in
    --engine|--allow-no-gpu|--min-driver-major|--custom-node-arch-list|--sage-arch-list|--sage)
      (($# >= 2)) || usage_error "$1 requires a value"
      case "$1" in
        --engine) requested_engine=$2 ;;
        --allow-no-gpu) allow_no_gpu=$2 ;;
        --min-driver-major) minimum_driver_major=$2 ;;
        --custom-node-arch-list) custom_node_architectures=$2 ;;
        --sage-arch-list) sage_architectures=$2 ;;
        --sage) sage_enabled=$2 ;;
      esac
      shift
      ;;
    *) usage_error "unknown option: $1" ;;
  esac
  shift
done

ok() {
  printf '[ok] %s\n' "$1"
}

warn() {
  printf '[warn] %s\n' "$1"
  warnings=$((warnings + 1))
}

fail() {
  printf '[fail] %s\n' "$1"
  failures=$((failures + 1))
}

check_engine() {
  local engine=$1
  local compose_version executable rootless runtime

  if ! command -v "$engine" >/dev/null 2>&1; then
    fail "$engine executable was not found"
    return
  fi
  executable=$(command -v "$engine")
  if windows_engine_executable "$executable"; then
    fail "WSL must use a Linux $engine client inside the distribution, not $executable"
    return
  fi

  ok "$("$engine" --version 2>/dev/null || printf '%s is installed' "$engine")"
  if "$engine" info >/dev/null 2>&1; then
    ok "$engine engine is reachable"
  else
    fail "$engine engine is not reachable"
    return
  fi

  if compose_version=$("$engine" compose version 2>/dev/null); then
    ok "$compose_version"
  else
    fail "$engine compose provider is unavailable"
    return
  fi

  if [[ "$engine" == podman ]]; then
    rootless=$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || true)
    if [[ "$rootless" == true ]]; then
      ok 'Podman engine is rootless'
    else
      fail 'the supported Podman path requires a rootless engine; rootful Podman needs a separate identity/storage design'
    fi
    runtime=$(podman info --format '{{.Host.OCIRuntime.Name}}' 2>/dev/null || true)
    if [[ "$runtime" == crun ]]; then
      ok 'Podman uses crun, which supports keep-groups'
    else
      fail "rootless Podman keep-groups requires crun (detected: ${runtime:-unknown})"
    fi
  fi
}

gpu_problem() {
  if [[ "$allow_no_gpu" == true ]]; then
    warn "$1 (--allow-no-gpu was supplied)"
  else
    fail "$1"
  fi
}

architecture_list_contains() {
  local list=${1//;/ }
  local capability=$2
  local architecture
  for architecture in $list; do
    architecture=${architecture%+PTX}
    [[ "$architecture" == "$capability" ]] && return 0
  done
  return 1
}

printf '== LatentCrate host doctor ==\n'
if [[ "$sage_enabled" == true ]]; then
  ok 'checking the SageAttention-capable image'
else
  ok 'checking the smaller image without SageAttention'
fi

for required_tool in flock sha256sum; do
  if command -v "$required_tool" >/dev/null 2>&1; then
    ok "$required_tool is available"
  else
    fail "$required_tool is required by the LatentCrate wrapper"
  fi
done

if [[ "$(uname -s 2>/dev/null)" == Linux ]]; then
  ok 'Linux execution environment detected'
  if [[ "$(uname -m 2>/dev/null)" == x86_64 ]]; then
    ok 'x86-64 host architecture detected'
  else
    fail "unsupported host architecture: $(uname -m 2>/dev/null || printf unknown); the pinned CUDA image is x86-64 only"
  fi
  if running_under_wsl; then
    warn 'WSL detected; complete the WSL2/NVIDIA validation checklist before describing this platform as tested'
  fi
else
  fail 'run LatentCrate in a Linux environment (native Linux or WSL2), not directly from Windows'
fi

if command -v id >/dev/null 2>&1; then
  actual_uid=$(id -u)
  actual_gid=$(id -g)
  if [[ "${HOST_UID:-$actual_uid}" == "$actual_uid" && "${HOST_GID:-$actual_gid}" == "$actual_gid" ]]; then
    ok "container identity matches the current user: ${actual_uid}:${actual_gid}"
  else
    warn "HOST_UID:HOST_GID=${HOST_UID:-unset}:${HOST_GID:-unset}, current user=${actual_uid}:${actual_gid}"
  fi
fi

if [[ -n "$requested_engine" ]]; then
  case "$requested_engine" in
    docker|podman)
      selected_engine=$requested_engine
      check_engine "$selected_engine"
      ;;
    *) fail "unsupported CONTAINER_ENGINE=$requested_engine" ;;
  esac
else
  for engine in podman docker; do
    if engine_usable "$engine"; then
      selected_engine=$engine
      break
    fi
  done
  if [[ -n "$selected_engine" ]]; then
    check_engine "$selected_engine"
    for engine in podman docker; do
      if [[ "$engine" != "$selected_engine" ]] && command -v "$engine" >/dev/null 2>&1 && ! engine_usable "$engine"; then
        warn "$engine is installed but is unreachable, lacks Compose, or does not meet the rootless Podman/crun requirements; automatic selection will use $selected_engine"
      fi
    done
  else
    fail 'neither Podman nor Docker has both a reachable engine and Compose provider'
    for engine in podman docker; do
      command -v "$engine" >/dev/null 2>&1 && check_engine "$engine"
    done
  fi
fi

if [[ -n "$selected_engine" ]]; then
  storage_root=
  if [[ "$selected_engine" == docker ]]; then
    storage_root=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null)
  else
    storage_root=$(podman info --format '{{.Store.GraphRoot}}' 2>/dev/null)
  fi
  [[ -n "$storage_root" && -d "$storage_root" ]] || storage_root=/
  free_kb=$(df -Pk -- "$storage_root" 2>/dev/null | awk 'NR == 2 {print $4}')
  if [[ "$free_kb" =~ ^[0-9]+$ ]]; then
    free_gb=$((free_kb / 1024 / 1024))
    if ((free_gb >= 75)); then
      ok "engine storage at $storage_root has ${free_gb} GB free"
    else
      warn "engine storage at $storage_root has only ${free_gb} GB free; a full LatentCrate build needs about 75 GB"
    fi
  else
    warn "could not measure free disk space for engine storage at $storage_root; ensure about 75 GB are free before building"
  fi
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  if gpu_summary=$(nvidia-smi --query-gpu=name,driver_version,compute_cap --format=csv,noheader 2>/dev/null); then
    ok "NVIDIA GPU: $gpu_summary"
    driver_version=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n 1)
    driver_major=${driver_version%%.*}
    if [[ "$driver_major" =~ ^[0-9]+$ ]] && ((driver_major >= minimum_driver_major)); then
      ok "NVIDIA driver $driver_version satisfies CUDA 13.x minimum major $minimum_driver_major"
    else
      gpu_problem "NVIDIA driver ${driver_version:-unknown} is older than the CUDA 13.x minimum major $minimum_driver_major"
    fi
    requested_device=${GPU_DEVICE:-nvidia.com/gpu=all}
    requested_selector=${requested_device#nvidia.com/gpu=}
    selected_capabilities=
    selected_gpu_found=false
    while IFS=',' read -r gpu_index gpu_uuid capability; do
      gpu_index=${gpu_index//[[:space:]]/}
      gpu_uuid=${gpu_uuid//[[:space:]]/}
      capability=${capability//[[:space:]]/}
      if [[ "$requested_selector" == all \
          || "$requested_selector" == "$gpu_index" \
          || "$requested_selector" == "$gpu_uuid" ]]; then
        selected_capabilities+="${capability}"$'\n'
        selected_gpu_found=true
      fi
    done < <(nvidia-smi --query-gpu=index,uuid,compute_cap --format=csv,noheader 2>/dev/null)
    if [[ "$selected_gpu_found" != true ]]; then
      warn "could not map GPU_DEVICE=$requested_device to an nvidia-smi index or GPU UUID; checking every GPU conservatively"
      selected_capabilities=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null)
    fi
    while IFS= read -r capability; do
      capability=${capability//[[:space:]]/}
      [[ -n "$capability" ]] || continue
      if architecture_list_contains "$custom_node_architectures" "$capability"; then
        ok "custom-node CUDA architecture list covers compute capability $capability"
      else
        gpu_problem "CUSTOM_NODE_CUDA_ARCH_LIST=$custom_node_architectures does not cover compute capability $capability"
      fi
      if architecture_list_contains "$sage_architectures" "$capability"; then
        ok "SageAttention CUDA architecture list covers compute capability $capability"
      elif [[ "$sage_enabled" == true ]]; then
        gpu_problem "SAGE_CUDA_ARCH_LIST=$sage_architectures does not cover compute capability $capability; update the Sage architecture list or rerun with --sage off"
      else
        warn "SAGE_CUDA_ARCH_LIST=$sage_architectures does not cover compute capability $capability; the Sage image is disabled"
      fi
    done <<< "$selected_capabilities"
  else
    gpu_problem 'nvidia-smi is installed but could not query a GPU'
  fi
else
  gpu_problem 'nvidia-smi was not found; the supported GPU path is unavailable'
fi

if command -v nvidia-ctk >/dev/null 2>&1; then
  requested_device=${GPU_DEVICE:-nvidia.com/gpu=all}
  if cdi_devices=$(nvidia-ctk cdi list 2>/dev/null) && grep -Fxq "$requested_device" <<< "$cdi_devices"; then
    ok "the CDI configuration lists the requested NVIDIA device: $requested_device"
    printf '%s\n' "$cdi_devices" | sed 's/^/       /'
  else
    gpu_problem "requested NVIDIA CDI device is unavailable: $requested_device"
  fi
else
  gpu_problem 'nvidia-ctk was not found; install NVIDIA Container Toolkit and generate CDI devices'
fi

requested_device=${GPU_DEVICE:-nvidia.com/gpu=all}
if [[ "$selected_engine" == docker ]]; then
  cdi_spec_dirs=$(docker info --format '{{range .CDISpecDirs}}{{.}}{{"\n"}}{{end}}' 2>/dev/null)
  if [[ -z "${cdi_spec_dirs//[[:space:]]/}" ]]; then
    warn "Docker reports no CDI spec directories; enable CDI in /etc/docker/daemon.json (\"features\": {\"cdi\": true}) or upgrade Docker so --device $requested_device can resolve"
  else
    mapfile -t cdi_dir_list < <(printf '%s\n' "$cdi_spec_dirs" | sed '/^[[:space:]]*$/d')
    if grep -Rqs -- 'nvidia.com/gpu' "${cdi_dir_list[@]}" 2>/dev/null; then
      ok "Docker CDI configuration looks correct: an nvidia.com/gpu spec exists in: ${cdi_dir_list[*]}"
    else
      warn "Docker CDI is enabled but no nvidia.com/gpu spec was found in: ${cdi_dir_list[*]}; run: sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml"
    fi
  fi
elif [[ "$selected_engine" == podman ]]; then
  if grep -Rqs -- 'nvidia.com/gpu' /etc/cdi /var/run/cdi 2>/dev/null; then
    ok 'Podman CDI configuration looks correct: an nvidia.com/gpu spec exists under /etc/cdi or /var/run/cdi'
  else
    warn "no nvidia.com/gpu CDI spec was found under /etc/cdi or /var/run/cdi; run: sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml, then verify with: podman run --rm --device $requested_device alpine true (pulls a small image)"
  fi
else
  warn "no engine was selected, so CDI resolution of $requested_device was not probed; after fixing the engine, verify with: <engine> run --rm --device $requested_device <image> true"
fi
printf '       The CDI checks above inspect configuration only. Real device resolution is proven by: bash bin/latentcrate smoke-gpu <profile>, or by the first up.\n'

if [[ -f /usr/share/containers/oci/hooks.d/oci-nvidia-hook.json ]]; then
  warn 'NVIDIA OCI hook-based device injection detected; CDI-only operation is recommended'
fi

for item in \
  "data:${COMFY_DATA_DIR:-./data/comfy}" \
  "models:${COMFY_MODELS_DIR:-./data/models}" \
  "cache:${COMFY_CACHE_DIR:-./data/cache}" \
  "local-nodes:${COMFY_LOCAL_NODES_DIR:-./local/custom_nodes}"; do
  label=${item%%:*}
  path=${item#*:}
  if [[ -d "$path" ]]; then
    ok "$label directory exists: $path"
    if [[ "$label" == models || "$label" == local-nodes ]]; then
      if [[ ! -r "$path" || ! -x "$path" ]]; then
        fail "$label directory is not readable/traversable: $path"
      fi
    elif [[ ! -w "$path" ]]; then
      fail "$label directory is not writable: $path"
    fi
  else
    warn "$label directory does not exist yet: $path (bin/latentcrate init creates it)"
  fi
done

managed_nodes_path=${COMFY_DATA_DIR:-./data/comfy}/custom_nodes
local_nodes_path=${COMFY_LOCAL_NODES_DIR:-./local/custom_nodes}
if [[ -d "$managed_nodes_path" && -d "$local_nodes_path" ]]; then
  managed_nodes_path=$(cd -- "$managed_nodes_path" && pwd -P)
  local_nodes_path=$(cd -- "$local_nodes_path" && pwd -P)
  case "$managed_nodes_path/" in
    "$local_nodes_path/"*) fail 'managed and local custom-node directories overlap' ;;
  esac
  case "$local_nodes_path/" in
    "$managed_nodes_path/"*) fail 'managed and local custom-node directories overlap' ;;
  esac
fi

models_path=${COMFY_MODELS_DIR:-./data/models}
if [[ -d "$models_path" ]] && command -v stat >/dev/null 2>&1; then
  models_gid=$(stat -c '%g' "$models_path" 2>/dev/null || true)
  if [[ "$selected_engine" == docker && -n "$models_gid" && "${HOST_MODEL_GID:-${HOST_GID:-}}" != "$models_gid" ]]; then
    warn "models directory GID is $models_gid but HOST_MODEL_GID=${HOST_MODEL_GID:-unset}; Docker may lack group access"
  fi
fi

case "${COMFY_BIND_ADDRESS:-127.0.0.1}" in
  127.0.0.1|localhost|::1|'[::1]') ;;
  *) warn "ComfyUI will bind beyond localhost: ${COMFY_BIND_ADDRESS}" ;;
esac

port_note=
if running_under_wsl; then
  port_note='; note that under WSL2 a Windows-side program can hold a port invisibly to Linux tools (check with netstat -ano from Windows)'
fi
if command -v ss >/dev/null 2>&1 && ss -H -ltn "sport = :${COMFY_PORT:-4207}" 2>/dev/null | grep -q .; then
  warn "host port ${COMFY_PORT:-4207} is already in use$port_note"
fi

printf '\nDoctor completed with %d failure(s) and %d warning(s).\n' "$failures" "$warnings"
((failures == 0))
