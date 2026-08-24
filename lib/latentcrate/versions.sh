# shellcheck shell=bash

readonly -a VERSION_PROFILE_INPUT_KEYS=(
  PYTORCH_DEVEL_IMAGE
  PYTORCH_RUNTIME_IMAGE
  COMFYUI_REF
  COMFYUI_FRONTEND_REF
  COMFY_FRONTEND_DIST_SHA256
  FFMPEG_REF
  NV_CODEC_HEADERS_REF
  SVT_AV1_REF
  SAGEATTENTION_REF
  TORCHCODEC_VERSION
  TORCHCODEC_INDEX_URL
  FRONTEND_NODE_IMAGE
  FRONTEND_PNPM_VERSION
  TOOL_PYTHON_IMAGE
)

readonly -a VERSION_COMPONENTS=(
  comfyui
  frontend
  ffmpeg
  nv-codec-headers
  svt-av1
  sageattention
  pytorch
  node
  pnpm
  tool-python
  torchcodec
)

version_component_supported() {
  local candidate
  [[ "$1" == all ]] && return 0
  for candidate in "${VERSION_COMPONENTS[@]}"; do
    [[ "$1" != "$candidate" ]] || return 0
  done
  return 1
}

version_update_key_allowed() {
  case "$1:$2" in
    comfyui:COMFYUI_REF|\
    frontend:COMFYUI_FRONTEND_REF|\
    frontend:COMFY_FRONTEND_DIST_SHA256|\
    ffmpeg:FFMPEG_REF|\
    nv-codec-headers:NV_CODEC_HEADERS_REF|\
    svt-av1:SVT_AV1_REF|\
    sageattention:SAGEATTENTION_REF|\
    pytorch:PYTORCH_DEVEL_IMAGE|\
    pytorch:PYTORCH_RUNTIME_IMAGE|\
    node:FRONTEND_NODE_IMAGE|\
    pnpm:FRONTEND_PNPM_VERSION|\
    tool-python:TOOL_PYTHON_IMAGE|\
    torchcodec:TORCHCODEC_VERSION)
      return 0
      ;;
    *) return 1 ;;
  esac
}

version_update_value_valid() {
  local key=$1
  local value=$2
  local numeric='[0-9]+(\.[0-9]+)+'

  case "$key" in
    COMFYUI_REF|SVT_AV1_REF|SAGEATTENTION_REF)
      [[ "$value" =~ ^v${numeric}$ ]]
      ;;
    COMFYUI_FRONTEND_REF)
      [[ "$value" =~ ^Comfy-Org/ComfyUI_frontend@v${numeric}$ ]]
      ;;
    COMFY_FRONTEND_DIST_SHA256)
      [[ "$value" =~ ^[0-9a-f]{64}$ ]]
      ;;
    FFMPEG_REF|NV_CODEC_HEADERS_REF)
      [[ "$value" =~ ^n${numeric}$ ]]
      ;;
    PYTORCH_DEVEL_IMAGE)
      [[ "$value" =~ ^docker\.io/pytorch/pytorch:${numeric}-cuda${numeric}-cudnn[0-9]+-devel$ ]]
      ;;
    PYTORCH_RUNTIME_IMAGE)
      [[ "$value" =~ ^docker\.io/pytorch/pytorch:${numeric}-cuda${numeric}-cudnn[0-9]+-runtime$ ]]
      ;;
    FRONTEND_NODE_IMAGE)
      [[ "$value" =~ ^docker\.io/library/node:[0-9]+(\.[0-9]+)*-bookworm-slim$ ]]
      ;;
    FRONTEND_PNPM_VERSION)
      [[ "$value" =~ ^${numeric}$ ]]
      ;;
    TOOL_PYTHON_IMAGE)
      [[ "$value" =~ ^docker\.io/library/python:${numeric}-slim-bookworm$ ]]
      ;;
    TORCHCODEC_VERSION)
      [[ "$value" =~ ^${numeric}(\+[A-Za-z0-9.]+)?$ ]]
      ;;
    *) return 1 ;;
  esac
}

version_component_input_key_required() {
  local component=$1
  local key=$2

  [[ "$component" == all ]] \
    || version_update_key_allowed "$component" "$key" \
    || [[ "$component:$key" == torchcodec:TORCHCODEC_INDEX_URL ]]
}

update_versions() (
  local selection=$1
  local profile=$2
  local path snapshot updated engine resolver_output line marker component key value extra result_selection result_count input_value
  local record_count=0 result_records=0 count
  local -a assignments=()
  local -a update_keys=()
  local -a update_values=()
  local -A seen_keys=()
  local -A component_counts=()
  local -A proposed_values=()

  version_component_supported "$selection" \
    || die "unknown version component: $selection"
  path=$(profile_file "$profile") || return
  acquire_version_profile_lock
  snapshot=$(snapshot_version_profile \
    "$path" "$PROJECT_ROOT/versions/.${profile}.version-update.XXXXXX") || return
  updated=
  cleanup_version_update() {
    rm -f -- "$snapshot"
    [[ -z "$updated" ]] || rm -f -- "$updated"
    release_version_profile_lock
  }
  trap cleanup_version_update EXIT

  for key in "${VERSION_PROFILE_INPUT_KEYS[@]}"; do
    version_component_input_key_required "$selection" "$key" || continue
    input_value=$(profile_assignment "$snapshot" "$key") || return
    assignments+=("$key=$input_value")
  done

  export LATENTCRATE_TOOLS_TAG=$profile
  engine=$(detect_engine) || return
  compose_tool "$engine" "$profile" build version-update || return
  resolver_output=$(compose_tool "$engine" "$profile" run --rm --no-deps -T \
    version-update resolve "$selection" "${assignments[@]}") || return

  while IFS= read -r line; do
    case "$line" in
      LATENTCRATE_VERSION_UPDATE\|*)
        IFS='|' read -r marker component key value extra <<< "$line"
        [[ "$marker" == LATENTCRATE_VERSION_UPDATE && -z "$extra" ]] \
          || die 'version resolver returned a malformed update record'
        [[ "$selection" == all || "$component" == "$selection" ]] \
          || die "version resolver returned an update for unexpected component: $component"
        version_update_key_allowed "$component" "$key" \
          || die "version resolver returned an unexpected update key: $component/$key"
        version_update_value_valid "$key" "$value" \
          || die "version resolver returned an invalid value for $key"
        [[ ! -v "seen_keys[$key]" ]] \
          || die "version resolver returned duplicate updates for $key"
        seen_keys["$key"]=1
        proposed_values["$key"]=$value
        component_counts["$component"]=$(( ${component_counts[$component]:-0} + 1 ))
        update_keys+=("$key")
        update_values+=("$value")
        ((record_count += 1))
        ;;
      LATENTCRATE_VERSION_RESULT\|*)
        IFS='|' read -r marker result_selection result_count extra <<< "$line"
        [[ "$marker" == LATENTCRATE_VERSION_RESULT && -z "$extra" \
            && "$result_selection" == "$selection" && "$result_count" =~ ^[0-9]+$ ]] \
          || die 'version resolver returned a malformed result record'
        ((result_records += 1))
        ;;
      LATENTCRATE_VERSION_*)
        die 'version resolver returned an unknown protocol record'
        ;;
    esac
  done <<< "$resolver_output"

  [[ "$result_records" == 1 ]] \
    || die 'version resolver did not return exactly one result record'
  [[ "$result_count" == "$record_count" ]] \
    || die 'version resolver update count did not match its result record'
  for component in frontend pytorch; do
    count=${component_counts[$component]:-0}
    [[ "$count" == 0 || "$count" == 2 ]] \
      || die "version resolver returned an incomplete $component update"
  done
  if [[ ${component_counts[pytorch]:-0} == 2 ]]; then
    [[ "${proposed_values[PYTORCH_DEVEL_IMAGE]%-devel}" \
        == "${proposed_values[PYTORCH_RUNTIME_IMAGE]%-runtime}" ]] \
      || die 'version resolver returned a mismatched PyTorch image pair'
  fi

  assert_version_profile_unchanged "$path" "$snapshot" 'the version update'
  if ((record_count == 0)); then
    printf 'Profile %s already has the latest eligible %s versions.\n' "$profile" "$selection"
    return
  fi

  updated=$(stage_version_profile_update \
    "$path" "$snapshot" "$PROJECT_ROOT/versions/.${profile}.version-update.XXXXXX") || return
  for ((count = 0; count < record_count; count++)); do
    key=${update_keys[$count]}
    value=${update_values[$count]}
    profile_assignment "$updated" "$key" >/dev/null
    sed -i -e "s|^${key}=.*|${key}=${value}|" "$updated"
  done

  publish_version_profile_update \
    "$path" "$snapshot" "$updated" 'the version update'
  updated=
  printf 'Updated %s version pin(s) in profile %s.\n' "$record_count" "$profile"
)
