# shellcheck shell=bash

acquire_node_dependency_lock() {
  command -v flock >/dev/null 2>&1 \
    || die 'flock (util-linux) is required for third-party node dependency updates'
  mkdir -p "$PROJECT_ROOT/build"
  exec {NODE_DEPS_LOCK_FD}>"$PROJECT_ROOT/build/.node-deps-lifecycle.lock"
  flock -n "$NODE_DEPS_LOCK_FD" \
    || die 'another third-party node dependency update or refresh build is running'
  local -a stale_candidates=("$PROJECT_ROOT"/build/.node-deps-buildable.*)
  if ((${#stale_candidates[@]} > 1)); then
    die 'multiple interrupted custom-node refresh transactions need manual review under build/'
  elif ((${#stale_candidates[@]} == 1)) \
      && [[ -d "${stale_candidates[0]}" ]]; then
    NODE_DEPS_CANDIDATE_BACKUP=${stale_candidates[0]}
    case "$(node_dependency_candidate_state)" in
      prepared|restoring)
        restore_node_dependency_candidate
        printf 'Recovered the last buildable third-party node dependency snapshot.\n'
        ;;
      committed)
        rm -rf -- "$NODE_DEPS_CANDIDATE_BACKUP"
        unset NODE_DEPS_CANDIDATE_BACKUP
        printf 'Completed cleanup from the last successful third-party node dependency build.\n'
        ;;
      '')
        rm -rf -- "$NODE_DEPS_CANDIDATE_BACKUP"
        unset NODE_DEPS_CANDIDATE_BACKUP
        printf 'Removed an incomplete custom-node refresh backup.\n'
        ;;
      *) die "invalid custom-node refresh journal: $NODE_DEPS_CANDIDATE_BACKUP/state" ;;
    esac
  fi
}

node_dependency_candidate_state() {
  [[ -n "${NODE_DEPS_CANDIDATE_BACKUP:-}" \
      && -f "$NODE_DEPS_CANDIDATE_BACKUP/state" ]] || return 0
  tr -d '\r\n' < "$NODE_DEPS_CANDIDATE_BACKUP/state"
}

write_node_dependency_candidate_state() {
  local state=$1
  local temporary
  [[ "$state" == prepared || "$state" == restoring || "$state" == committed ]] \
    || die "invalid custom-node refresh state: $state"
  temporary="$NODE_DEPS_CANDIDATE_BACKUP/.state.$$"
  printf '%s\n' "$state" > "$temporary"
  mv -f -- "$temporary" "$NODE_DEPS_CANDIDATE_BACKUP/state"
}

release_node_dependency_lock() {
  if [[ -n "${NODE_DEPS_LOCK_FD:-}" ]]; then
    flock -u "$NODE_DEPS_LOCK_FD" || true
    exec {NODE_DEPS_LOCK_FD}>&-
    unset NODE_DEPS_LOCK_FD
  fi
}

prepare_custom_node_cache_key() {
  local profile=${1:-current}
  local snapshot="$PROJECT_ROOT/build/custom-node-requirements"
  profile_file "$profile" >/dev/null
  [[ -d "$snapshot" ]] || die 'third-party node dependency snapshot is missing'
  CUSTOM_NODE_CACHE_KEY=$(bash "$PROJECT_ROOT/scripts/custom-node-cache-key.sh" \
    "$(effective_profile_value "$profile" PYTORCH_DEVEL_IMAGE)" \
    "$(effective_profile_value "$profile" PYTORCH_RUNTIME_IMAGE)" \
    "$(effective_profile_value "$profile" COMFYUI_REF)" \
    "$(effective_profile_value "$profile" TORCHCODEC_VERSION)" \
    "$(effective_profile_value "$profile" TORCHCODEC_INDEX_URL)" \
    "$(effective_profile_value "$profile" CUSTOM_NODE_CUDA_ARCH_LIST)") \
    || die 'could not identify the third-party node dependency build cache'
  export CUSTOM_NODE_CACHE_KEY
}

begin_node_dependency_candidate() {
  local snapshot="$PROJECT_ROOT/build/custom-node-requirements"
  NODE_DEPS_CANDIDATE_BACKUP=$(mktemp -d "$PROJECT_ROOT/build/.node-deps-buildable.XXXXXX")
  export NODE_DEPS_CANDIDATE_BACKUP
  if [[ -d "$snapshot" ]]; then
    cp -a -- "$snapshot" "$NODE_DEPS_CANDIDATE_BACKUP/snapshot"
  fi
  write_node_dependency_candidate_state prepared
}

restore_node_dependency_candidate() {
  local snapshot="$PROJECT_ROOT/build/custom-node-requirements" state
  [[ -n "${NODE_DEPS_CANDIDATE_BACKUP:-}" ]] || return
  state=$(node_dependency_candidate_state)
  if [[ "$state" == committed || -z "$state" ]]; then
    rm -rf -- "$NODE_DEPS_CANDIDATE_BACKUP"
    unset NODE_DEPS_CANDIDATE_BACKUP
    return
  fi
  [[ "$state" == prepared || "$state" == restoring ]] \
    || die "invalid custom-node refresh state: $state"
  write_node_dependency_candidate_state restoring
  if [[ -d "$NODE_DEPS_CANDIDATE_BACKUP/snapshot" ]]; then
    rm -rf -- "$snapshot"
    mv -- "$NODE_DEPS_CANDIDATE_BACKUP/snapshot" "$snapshot"
  elif [[ ! -d "$snapshot" ]]; then
    die 'custom-node refresh recovery has neither a saved nor a published snapshot'
  fi
  rm -rf -- "$NODE_DEPS_CANDIDATE_BACKUP"
  unset NODE_DEPS_CANDIDATE_BACKUP
}

commit_node_dependency_candidate() {
  local backup
  [[ -n "${NODE_DEPS_CANDIDATE_BACKUP:-}" ]] || return
  backup=$NODE_DEPS_CANDIDATE_BACKUP
  write_node_dependency_candidate_state committed
  unset NODE_DEPS_CANDIDATE_BACKUP
  rm -rf -- "$backup"
}

snapshot_node_dependencies() {
  local profile=${1:-current}
  local custom_nodes_dir="${COMFY_DATA_DIR:-$PROJECT_ROOT/data/comfy}/custom_nodes"
  local local_nodes_dir="${COMFY_LOCAL_NODES_DIR:-$PROJECT_ROOT/local/custom_nodes}"
  local destination="$PROJECT_ROOT/build/custom-node-requirements"
  local engine

  [[ -d "$custom_nodes_dir" ]] \
    || die "custom-node directory does not exist: $custom_nodes_dir (check COMFY_DATA_DIR and start ComfyUI once)"
  [[ -d "$local_nodes_dir" ]] \
    || die "local custom-node directory does not exist: $local_nodes_dir (check COMFY_LOCAL_NODES_DIR or run latentcrate init)"
  profile_file "$profile" >/dev/null
  validate_custom_node_roots
  custom_nodes_dir=$(cd -- "$custom_nodes_dir" && pwd -P)
  local_nodes_dir=$(cd -- "$local_nodes_dir" && pwd -P)
  mkdir -p "$PROJECT_ROOT/build"
  export NODE_DEPS_SOURCE_DIR=$custom_nodes_dir
  export NODE_DEPS_LOCAL_SOURCE_DIR=$local_nodes_dir
  export NODE_DEPS_OUTPUT_DIR=$PROJECT_ROOT/build
  export LATENTCRATE_TOOLS_TAG=$profile

  engine=$(detect_engine)
  printf '== Building the LatentCrate tools image ==\n'
  compose_tool "$engine" "$profile" build node-deps-snapshot || return
  printf '== Capturing the third-party node dependency snapshot ==\n'
  compose_tool "$engine" "$profile" run --rm --no-deps node-deps-snapshot || return
  [[ -r "$destination/manifest.txt" ]] \
    || { printf 'LatentCrate: dependency capture completed without publishing manifest.txt\n' >&2; return 1; }
  printf 'Rebuild the selected image to install the saved dependencies.\n'
}

clear_node_dependencies() {
  local destination="$PROJECT_ROOT/build/custom-node-requirements"
  rm -rf -- "$destination"
  mkdir -p "$destination"
  : > "$destination/.gitkeep"
  printf 'Cleared the saved third-party node requirements.\n'
}

announce_comfy_image_build() {
  printf '== Building the ComfyUI image ==\n'
  printf 'The first build can take a long time (possibly hours).\n'
}

# Shared refresh-build transaction used by both `up` and `build`:
# lock -> trap -> begin candidate -> snapshot -> cache key -> build ->
# commit (or restore on failure) -> release. The commands differ only in
# the message they report when the image build fails.
run_refreshed_build() {
  local engine=$1
  local profile=$2
  local build_failure_message=$3

  acquire_node_dependency_lock
  trap 'restore_node_dependency_candidate; release_node_dependency_lock' EXIT
  begin_node_dependency_candidate
  if ! snapshot_node_dependencies "$profile"; then
    restore_node_dependency_candidate
    die 'third-party node dependency capture failed; the last snapshot was preserved'
  fi
  prepare_custom_node_cache_key "$profile"
  announce_comfy_image_build
  if ! compose "$engine" "$profile" build comfy; then
    restore_node_dependency_candidate
    die "$build_failure_message"
  fi
  commit_node_dependency_candidate
  release_node_dependency_lock
  trap - EXIT
}

# Shared cached-snapshot build used by `up --use-saved-node-deps` and
# `build --use-saved-node-deps`: lock -> cache key -> build -> release.
run_cached_build() {
  local engine=$1
  local profile=$2

  acquire_node_dependency_lock
  trap 'release_node_dependency_lock' EXIT
  prepare_custom_node_cache_key "$profile"
  announce_comfy_image_build
  if ! compose "$engine" "$profile" build comfy; then
    die 'image build failed'
  fi
  release_node_dependency_lock
  trap - EXIT
}
