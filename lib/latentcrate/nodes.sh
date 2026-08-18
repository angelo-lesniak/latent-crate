# shellcheck shell=bash

node_set_file() {
  local set_name=$1
  local path

  [[ "$set_name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || die "unsafe custom-node set name: $set_name"
  path="$PROJECT_ROOT/config/custom-nodes/sets/${set_name}.toml"
  [[ -f "$path" ]] || die "unknown custom-node set: $set_name"
  printf '%s\n' "$path"
}

acquire_node_set_lock() {
  command -v flock >/dev/null 2>&1 \
    || die 'flock (util-linux) is required for custom-node set operations'
  mkdir -p "$PROJECT_ROOT/build"
  exec {NODE_SET_LOCK_FD}>"$PROJECT_ROOT/build/.node-set-lifecycle.lock"
  flock -n "$NODE_SET_LOCK_FD" \
    || die 'another custom-node set operation is running'
}

release_node_set_lock() {
  if [[ -n "${NODE_SET_LOCK_FD:-}" ]]; then
    flock -u "$NODE_SET_LOCK_FD" || true
    exec {NODE_SET_LOCK_FD}>&-
    unset NODE_SET_LOCK_FD
  fi
}

list_node_sets() {
  local path name description
  for path in "$PROJECT_ROOT"/config/custom-nodes/sets/*.toml; do
    [[ -f "$path" ]] || continue
    name=$(basename "$path" .toml)
    # Top-level `description` string from the set manifest (the same field
    # scripts/manage-node-set.py reads).
    description=$(sed -n 's/^description[[:space:]]*=[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' "$path" | head -n 1)
    if [[ -n "$description" ]]; then
      printf '%s - %s\n' "$name" "$description"
    else
      printf '%s\n' "$name"
    fi
  done
}

run_node_set() {
  local action=$1
  local set_name=$2
  local profile=${3:-current}
  local target="${COMFY_DATA_DIR:-$PROJECT_ROOT/data/comfy}/custom_nodes"
  local engine running

  profile_file "$profile" >/dev/null
  acquire_node_set_lock
  trap 'release_node_set_lock' EXIT
  [[ -d "$target" ]] \
    || die "custom-node directory does not exist: $target (run latentcrate init first)"
  target=$(cd -- "$target" && pwd -P)
  export NODE_SET_ACTION=$action
  export NODE_SET_FILE
  NODE_SET_FILE=$(node_set_file "$set_name")
  export NODE_SET_TARGET_DIR=$target
  export LATENTCRATE_TOOLS_TAG=$profile
  engine=$(detect_engine)
  if [[ "$action" == install || "$action" == sync ]]; then
    running=$(running_service_container_ids "$engine" comfy) \
      || die 'could not determine whether ComfyUI is running; no custom nodes were changed'
    [[ -z "$running" ]] \
      || die "stop ComfyUI before installing or syncing a custom-node set: bash bin/latentcrate down $profile"
  fi
  if [[ "$action" == status ]]; then
    compose_tool "$engine" "$profile" build node-set-status
    compose_tool "$engine" "$profile" run --rm --no-deps node-set-status
  else
    compose_tool "$engine" "$profile" build node-set
    compose_tool "$engine" "$profile" run --rm --no-deps node-set
  fi
  if [[ "$action" == install || "$action" == sync ]]; then
    printf 'Run "bin/latentcrate up %s" to build the node dependencies into the image.\n' "$profile"
  fi
  release_node_set_lock
  trap - EXIT
}
