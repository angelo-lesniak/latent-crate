# shellcheck shell=bash

# Frontend Git reference resolution, the variant -> image target/tag mapping,
# and the hardened Git invocation live in scripts/resolve-frontend.sh, which
# is shared with .github/workflows/build.yml.

resolve_frontend_git_ref() {
  local url=$1
  local requested=$2

  bash "$PROJECT_ROOT/scripts/resolve-frontend.sh" ref "$url" "$requested"
}

prepare_frontend_mode() {
  local profile=$1
  local mode url requested resolved dist source source_id cache_root output

  FRONTEND_SOURCE_BUILD=false

  mode=${COMFY_FRONTEND_MODE:-$(profile_value "$profile" COMFY_FRONTEND_MODE)}
  mode=${mode:-release}

  case "$mode" in
    release)
      export COMFY_FRONTEND_MODE=release
      ;;
    git)
      url=${FRONTEND_GIT_URL:-$(profile_value "$profile" FRONTEND_GIT_URL)}
      requested=${FRONTEND_GIT_REQUESTED_REF:-${FRONTEND_GIT_REF:-$(profile_value "$profile" FRONTEND_GIT_REF)}}
      resolved=$(resolve_frontend_git_ref "$url" "$requested")
      export COMFY_FRONTEND_MODE=git
      export FRONTEND_GIT_URL=$url
      export FRONTEND_GIT_REQUESTED_REF=$requested
      export FRONTEND_GIT_REF=$resolved
      ;;
    dist)
      dist=${COMFY_FRONTEND_DIST_DIR:-}
      [[ -n "$dist" ]] || die 'dist frontend mode requires COMFY_FRONTEND_DIST_DIR or --frontend-dist'
      [[ -r "$dist/index.html" ]] || die "frontend dist/index.html is missing or unreadable: $dist"
      dist=$(cd -- "$dist" && pwd -P)
      if find "$dist" -type l -print -quit | grep -q .; then
        die "frontend dist tree must not contain symbolic links: $dist"
      fi
      export COMFY_FRONTEND_MODE=dist
      export COMFY_FRONTEND_DIST_DIR=$dist
      ;;
    source)
      source=${COMFY_FRONTEND_SOURCE_DIR:-}
      [[ -n "$source" ]] \
        || die 'frontend source mode requires COMFY_FRONTEND_SOURCE_DIR or --frontend-source'
      [[ -d "$source" ]] || die "frontend source directory does not exist: $source"
      [[ -r "$source/package.json" ]] \
        || die "frontend source package.json is missing or unreadable: $source"
      [[ -r "$source/pnpm-lock.yaml" ]] \
        || die "frontend source pnpm-lock.yaml is missing or unreadable: $source"
      command -v sha256sum >/dev/null 2>&1 \
        || die 'sha256sum is required to identify a local frontend source checkout'
      source=$(cd -- "$source" && pwd -P)
      source_id=$(printf '%s\0' "$source" | sha256sum | cut -c 1-16)
      cache_root=${COMFY_CACHE_DIR:-$PROJECT_ROOT/data/cache}
      if [[ "$cache_root" != /* ]]; then
        cache_root="$PROJECT_ROOT/${cache_root#./}"
      fi
      output=${FRONTEND_OUTPUT_DIR:-$cache_root/frontend-builds/$profile/$source_id}
      if [[ "$output" != /* ]]; then
        output="$PROJECT_ROOT/${output#./}"
      fi
      export COMFY_FRONTEND_SOURCE_DIR=$source
      export FRONTEND_SOURCE_DIR=$source
      export FRONTEND_OUTPUT_DIR=$output
      export FRONTEND_PNPM_CACHE_DIR=$cache_root/frontend-pnpm
      export FRONTEND_WORK_DIR=$cache_root/frontend-work/$profile/${source_id}.${BASHPID}
      export COMFY_FRONTEND_DIST_DIR=$output/current/dist
      export COMFY_FRONTEND_MODE=dist
      FRONTEND_SOURCE_BUILD=true
      ;;
    *) die "unsupported COMFY_FRONTEND_MODE=$mode" ;;
  esac
}

frontend_tree_digest() {
  local root=$1

  for required in find sort xargs sha256sum; do
    command -v "$required" >/dev/null 2>&1 \
      || die "$required is required to verify a local frontend tree"
  done
  (
    export LC_ALL=C
    cd "$root" || exit 1
    find . -type f -print0 \
      | sort -z \
      | xargs -0 sha256sum \
      | sha256sum \
      | cut -d ' ' -f 1
  )
}

configure_variant() {
  local profile=$1
  local sage=$2
  local variant target tag

  prepare_frontend_mode "$profile"
  if is_true "${COMFY_GLOBAL_SAGE:-false}"; then
    sage=true
  fi

  case "${COMFY_FRONTEND_MODE}:${sage}" in
    release:false|release:true|dist:false|dist:true) variant=release ;;
    git:false|git:true) variant=frontend-git ;;
    *) die 'invalid frontend/Sage image combination' ;;
  esac
  [[ "$sage" == true ]] && variant="${variant}-sage"

  target=$(bash "$PROJECT_ROOT/scripts/resolve-frontend.sh" target "$variant") \
    || die 'invalid frontend/Sage image combination'
  tag=$(bash "$PROJECT_ROOT/scripts/resolve-frontend.sh" tag "$profile" "$variant") \
    || die 'invalid frontend/Sage image combination'
  export COMFY_BUILD_TARGET=$target
  export LATENTCRATE_TAG=$tag
  prepare_custom_node_cache_key "$profile"
  SAGE=$sage
}

build_local_frontend() {
  local engine=$1
  local profile=$2

  mkdir -p "$FRONTEND_OUTPUT_DIR" "$FRONTEND_PNPM_CACHE_DIR" "$FRONTEND_WORK_DIR"
  export LATENTCRATE_TOOLS_TAG=$profile
  compose_tool "$engine" "$profile" build frontend-fetch
  compose_tool "$engine" "$profile" run --rm --no-deps frontend-fetch
  compose_tool "$engine" "$profile" run --rm --no-deps frontend-build
  [[ -r "$COMFY_FRONTEND_DIST_DIR/index.html" ]] \
    || die 'frontend builder completed without publishing dist/index.html'
  rmdir -- "$FRONTEND_WORK_DIR" 2>/dev/null \
    || printf 'LatentCrate: temporary frontend files remain in %s; they are safe to remove after review\n' \
      "$FRONTEND_WORK_DIR" >&2
}
