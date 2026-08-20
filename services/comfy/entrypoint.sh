#!/usr/bin/env bash
set -Eeuo pipefail

readonly COMFYUI_ROOT=/opt/comfyui
readonly IMAGE_FRONTEND_ROOT=/opt/latentcrate-frontend
readonly DIST_FRONTEND_ROOT=/opt/latentcrate-frontend-dist
readonly DATA_ROOT=/data
readonly MODELS_ROOT=/models
readonly CACHE_ROOT=/cache
readonly LOCAL_NODES_ROOT=/local/custom_nodes

frontend_mode=${COMFY_FRONTEND_MODE:-${LATENTCRATE_FRONTEND_IMAGE_MODE:-release}}
frontend_root=${COMFY_FRONTEND_ROOT:-$IMAGE_FRONTEND_ROOT}

case "$frontend_mode" in
  release|git)
    if [[ "$frontend_mode" != "${LATENTCRATE_FRONTEND_IMAGE_MODE:-release}" ]]; then
      printf 'LatentCrate: requested frontend mode %s does not match image mode %s.\n' \
        "$frontend_mode" "${LATENTCRATE_FRONTEND_IMAGE_MODE:-release}" >&2
      exit 1
    fi
    if [[ "$frontend_root" != "$IMAGE_FRONTEND_ROOT" ]]; then
      printf 'LatentCrate: image frontend mode must use %s.\n' "$IMAGE_FRONTEND_ROOT" >&2
      exit 1
    fi
    ;;
  dist)
    if [[ "$frontend_root" != "$DIST_FRONTEND_ROOT" ]]; then
      printf 'LatentCrate: dist frontend mode must use the read-only mount at %s.\n' \
        "$DIST_FRONTEND_ROOT" >&2
      exit 1
    fi
    ;;
  *)
    printf 'LatentCrate: unsupported frontend mode: %s\n' "$frontend_mode" >&2
    exit 1
    ;;
esac

ensure_directory() {
  local path=$1

  if [[ -e "$path" && ! -d "$path" ]]; then
    printf 'LatentCrate: expected a directory at %s\n' "$path" >&2
    exit 1
  fi

  mkdir -p "$path"
}

ensure_writable_directory() {
  local path=$1
  local probe

  ensure_directory "$path"
  probe="${path}/.latentcrate-write-test-$$"
  if ! : > "$probe"; then
    printf 'LatentCrate: directory is not writable: %s\n' "$path" >&2
    exit 1
  fi
  rm -f "$probe"
}

ensure_readable_directory() {
  local path=$1

  if [[ ! -d "$path" || ! -r "$path" || ! -x "$path" ]]; then
    printf 'LatentCrate: directory is not readable/traversable: %s\n' "$path" >&2
    exit 1
  fi
}

reject_duplicate_custom_nodes() {
  local managed_path managed_name local_path local_name

  shopt -s nullglob
  for local_path in "$LOCAL_NODES_ROOT"/*; do
    [[ -d "$local_path" && "${local_path##*/}" != *.disabled ]] || continue
    local_name=${local_path##*/}
    for managed_path in "$DATA_ROOT/custom_nodes"/*; do
      [[ -d "$managed_path" && "${managed_path##*/}" != *.disabled ]] || continue
      managed_name=${managed_path##*/}
      if [[ "${managed_name,,}" == "${local_name,,}" ]]; then
        printf 'LatentCrate: duplicate custom-node name in managed and local sources: %s / %s\n' \
          "$managed_name" "$local_name" >&2
        exit 1
      fi
    done
  done
  shopt -u nullglob
}

reject_hidden_custom_nodes() {
  local node_path root

  shopt -s nullglob dotglob
  for root in "$DATA_ROOT/custom_nodes" "$LOCAL_NODES_ROOT"; do
    for node_path in "$root"/.*; do
      [[ -d "$node_path" ]] || continue
      case "${node_path##*/}" in
        .|..) continue ;;
        .latentcrate-node-set-*.disabled)
          printf 'LatentCrate: interrupted custom-node set transaction detected: %s; run the matching "nodes install" or "nodes sync" command to recover it.\n' \
            "$node_path" >&2
          exit 1
          ;;
      esac
      printf 'LatentCrate: hidden custom-node directories are not supported: %s\n' \
        "$node_path" >&2
      exit 1
    done
  done
  shopt -u dotglob nullglob
}

is_true() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

initialize_manager_config() {
  local protected_config="$DATA_ROOT/user/__manager/config.ini"
  local alternate_config="$DATA_ROOT/user/default/ComfyUI-Manager/config.ini"
  local staging

  if [[ -e "$protected_config" || -e "$alternate_config" ]]; then
    return
  fi

  ensure_writable_directory "$(dirname "$protected_config")"
  staging="${protected_config}.latentcrate-$$"
  cp /etc/latentcrate/manager-config.ini "$staging"
  chmod 0644 "$staging"
  mv "$staging" "$protected_config"
  printf 'LatentCrate: initialized default ComfyUI Manager security and privacy settings at %s\n' \
    "$protected_config"
}

umask "${UMASK:-0002}"

ensure_writable_directory "$DATA_ROOT"
ensure_writable_directory "$CACHE_ROOT"
ensure_directory "$MODELS_ROOT"
ensure_readable_directory "$LOCAL_NODES_ROOT"

for path in \
  "$DATA_ROOT/custom_nodes" \
  "$DATA_ROOT/home" \
  "$DATA_ROOT/input" \
  "$DATA_ROOT/output" \
  "$DATA_ROOT/user" \
  "$CACHE_ROOT/huggingface" \
  "$CACHE_ROOT/pip" \
  "$CACHE_ROOT/temp" \
  "$CACHE_ROOT/torch-extensions" \
  "$CACHE_ROOT/triton" \
  "$CACHE_ROOT/xdg"; do
  ensure_writable_directory "$path"
done

reject_hidden_custom_nodes
reject_duplicate_custom_nodes

if [[ ! -r "$MODELS_ROOT" || ! -x "$MODELS_ROOT" ]]; then
  printf 'LatentCrate: models directory is not readable/traversable: %s\n' "$MODELS_ROOT" >&2
  exit 1
fi

if is_true "${COMFY_ENABLE_MANAGER:-true}"; then
  initialize_manager_config
fi

if [[ ! -r "$frontend_root/index.html" ]]; then
  printf 'LatentCrate: frontend index is missing or unreadable: %s\n' "$frontend_root" >&2
  exit 1
fi

export HOME="$DATA_ROOT/home"
image_environment_id=$(cut -c 1-12 /usr/local/share/latentcrate/python-environment.id 2>/dev/null || printf unknown)
runtime_id=$(python -c 'import sys, torch; cuda = (torch.version.cuda or "cpu").replace(".", ""); print(f"py{sys.version_info.major}{sys.version_info.minor}-torch{torch.__version__.split(chr(43))[0]}-cuda{cuda}")')
runtime_id="${runtime_id}-env${image_environment_id}"
export PYTHONUSERBASE="$CACHE_ROOT/python-user/$runtime_id"
export TORCH_EXTENSIONS_DIR="$CACHE_ROOT/torch-extensions/$runtime_id"
export TRITON_CACHE_DIR="$CACHE_ROOT/triton/$runtime_id"
ensure_writable_directory "$PYTHONUSERBASE"
ensure_writable_directory "$TORCH_EXTENSIONS_DIR"
ensure_writable_directory "$TRITON_CACHE_DIR"
export PATH="$PYTHONUSERBASE/bin:$PATH"
unset PYTHONNOUSERSITE

printf 'LatentCrate runtime\n'
printf '  ComfyUI source: %s\n' "$(cat /usr/local/share/latentcrate/comfyui.commit 2>/dev/null || printf unknown)"
printf '  ComfyUI frontend version: %s\n' "$(cat /usr/local/share/latentcrate/frontend.ref 2>/dev/null || printf unknown)"
printf '  Frontend mode/root: %s | %s\n' "$frontend_mode" "$frontend_root"
printf '  SageAttention image: %s\n' "${LATENTCRATE_SAGE_ENABLED:-0}"
printf '  Data: %s | Models: %s | Cache: %s\n' "$DATA_ROOT" "$MODELS_ROOT" "$CACHE_ROOT"
printf '  Runtime package cache: %s\n' "$PYTHONUSERBASE"

if ! python -m pip check; then
  printf 'LatentCrate: cached runtime packages have dependency conflicts.\n' >&2
  printf 'Snapshot node requirements, rebuild the image, then clear %s.\n' "$PYTHONUSERBASE" >&2
fi

if (($# > 0)); then
  exec "$@"
fi

args=(
  python -u main.py
  --listen 0.0.0.0
  # Port 8188 must match the container port in compose.yaml and the probe URL
  # in services/comfy/healthcheck.py.
  --port 8188
  --base-directory "$DATA_ROOT"
  --models-directory "$MODELS_ROOT"
  --user-directory "$DATA_ROOT/user"
  # The database path must match services/comfy/healthcheck.py.
  --database-url "sqlite:///$DATA_ROOT/user/comfyui.db"
  --temp-directory "$CACHE_ROOT"
  --front-end-root "$frontend_root"
  --extra-model-paths-config /etc/latentcrate/extra_model_paths.yaml
  --disable-auto-launch
)

if is_true "${COMFY_ENABLE_MANAGER:-true}"; then
  args+=(--enable-manager)
fi

append_sage_args() {
  case "${LATENTCRATE_SAGE_MODE:-available}" in
    off|available) ;;
    global)
      if [[ "${LATENTCRATE_SAGE_ENABLED:-0}" != 1 ]]; then
        printf 'LatentCrate: LATENTCRATE_SAGE=global requires the runtime-sage image target.\n' >&2
        exit 1
      fi
      args+=(--use-sage-attention)
      ;;
    *)
      printf 'LatentCrate: LATENTCRATE_SAGE_MODE must be off, available, or global, not: %s\n' \
        "${LATENTCRATE_SAGE_MODE}" >&2
      exit 1
      ;;
  esac
}
append_sage_args

if is_true "${COMFY_DISABLE_API_NODES:-false}"; then
  args+=(--disable-api-nodes)
fi

if is_true "${COMFY_DISABLE_METADATA:-false}"; then
  args+=(--disable-metadata)
fi

if [[ -n "${COMFYUI_EXTRA_ARGS:-}" ]]; then
  # Extra arguments are intentionally whitespace-delimited. Put paths containing
  # spaces in the fixed configuration variables instead of this advanced option.
  read -r -a extra_args <<< "$COMFYUI_EXTRA_ARGS"
  args+=("${extra_args[@]}")
fi

cd "$COMFYUI_ROOT"
exec "${args[@]}"
