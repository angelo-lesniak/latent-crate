# shellcheck shell=bash

[[ -n "${PROJECT_ROOT:-}" ]] \
  || { printf 'LatentCrate: PROJECT_ROOT must be set before loading models.sh\n' >&2; return 1; }

model_set_file() {
  local set_name=$1
  local path

  [[ "$set_name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || die "unsafe model-set name: $set_name"
  path="$PROJECT_ROOT/config/model-sets/${set_name}.toml"
  [[ -f "$path" ]] || die "unknown model set: $set_name (run: bash bin/latentcrate models list)"
  printf '%s\n' "$path"
}

capture_hf_token() {
  # load_local_env exports .env values for Compose interpolation. Keep this
  # secret in an unexported shell variable instead, even for commands that do
  # not use model sets.
  if [[ -v HF_TOKEN ]]; then
    LATENTCRATE_HF_TOKEN_VALUE=$HF_TOKEN
  else
    unset LATENTCRATE_HF_TOKEN_VALUE || true
  fi
  export -n LATENTCRATE_HF_TOKEN_VALUE 2>/dev/null || true
  unset HF_TOKEN || true
}

list_model_sets() {
  local path name description
  for path in "$PROJECT_ROOT"/config/model-sets/*.toml; do
    [[ -f "$path" ]] || continue
    name=$(basename "$path" .toml)
    description=$(sed -n 's/^description[[:space:]]*=[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' "$path" | head -n 1)
    if [[ -n "$description" ]]; then
      printf '%s - %s\n' "$name" "$description"
    else
      printf '%s\n' "$name"
    fi
  done
}

validate_model_set_selection() {
  local name
  (($# > 0)) || die 'provide one or more model-set names, or all'
  if (($# > 1)); then
    for name in "$@"; do
      [[ "$name" != all ]] || die 'all cannot be combined with named model sets'
    done
  fi
  for name in "$@"; do
    [[ "$name" == all ]] || model_set_file "$name" >/dev/null
  done
}

run_model_sets() {
  local action=$1
  local profile=$2
  shift 2
  local models_dir="${COMFY_MODELS_DIR:-$PROJECT_ROOT/data/models}"
  local hf_cache_dir="${COMFY_CACHE_DIR:-$PROJECT_ROOT/data/cache}/huggingface"
  local engine token service
  local -a selected=("$@")

  [[ "$action" == fetch || "$action" == status ]] \
    || die "unsupported model-set action: $action"
  profile_file "$profile" >/dev/null
  validate_model_set_selection "${selected[@]}"
  [[ -d "$models_dir" ]] \
    || die "models directory does not exist: $models_dir (run latentcrate init first)"
  models_dir=$(cd -- "$models_dir" && pwd -P)

  # Keep the token only in this shell variable. In particular, do not let
  # Compose copy it into helper or ComfyUI container environments.
  token=${LATENTCRATE_HF_TOKEN_VALUE:-}
  unset LATENTCRATE_HF_TOKEN_VALUE || true
  export COMFY_MODELS_DIR=$models_dir
  export LATENTCRATE_TOOLS_TAG=$profile
  engine=$(detect_engine)

  if [[ "$action" == status ]]; then
    service=model-set-status
    compose_tool "$engine" "$profile" build "$service"
    compose_tool "$engine" "$profile" run --rm --no-deps -T "$service" \
      status "${selected[@]}"
  else
    mkdir -p "$hf_cache_dir"
    service=model-set
    compose_tool "$engine" "$profile" build "$service"
    # printf is a Bash builtin, so the token is not exposed in an external
    # process argument. The helper reads it once and never persists it.
    printf '%s' "$token" | compose_tool "$engine" "$profile" run --rm --no-deps -T "$service" \
      fetch --token-stdin "${selected[@]}"
  fi
}
