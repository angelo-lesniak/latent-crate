# shellcheck shell=bash

# Frontend Git reference resolution and the hardened Git invocation live in
# scripts/resolve-frontend.sh.

FRONTEND_RELEASE_URL=https://github.com/Comfy-Org/ComfyUI_frontend

resolve_frontend_git_ref() {
  local url=$1
  local requested=$2

  bash "$PROJECT_ROOT/scripts/resolve-frontend.sh" ref "$url" "$requested"
}

# The release frontend is the pinned tag built from Git. Commands that only
# render the project (down, status, logs) pass RESOLVE_FRONTEND_REF=false and
# export the requested reference unresolved, so they need no network.
prepare_frontend_mode() {
  local mode url requested resolved dist source source_id cache_root output

  FRONTEND_SOURCE_BUILD=false

  mode=${COMFY_FRONTEND_MODE:-release}

  case "$mode" in
    release|git)
      if [[ "$mode" == release ]]; then
        url=$FRONTEND_RELEASE_URL
        requested=$COMFYUI_FRONTEND_REF
      else
        url=${FRONTEND_GIT_URL:-}
        requested=${FRONTEND_GIT_REQUESTED_REF:-${FRONTEND_GIT_REF:-}}
      fi
      resolved=$requested
      if [[ "${RESOLVE_FRONTEND_REF:-true}" == true ]]; then
        resolved=$(resolve_frontend_git_ref "$url" "$requested")
      fi
      export COMFY_FRONTEND_MODE=$mode
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
      export FRONTEND_GIT_URL='' FRONTEND_GIT_REQUESTED_REF='' FRONTEND_GIT_REF=''
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
      output=${FRONTEND_OUTPUT_DIR:-$cache_root/frontend-builds/$source_id}
      if [[ "$output" != /* ]]; then
        output="$PROJECT_ROOT/${output#./}"
      fi
      export COMFY_FRONTEND_SOURCE_DIR=$source
      export FRONTEND_SOURCE_DIR=$source
      export FRONTEND_OUTPUT_DIR=$output
      export FRONTEND_PNPM_CACHE_DIR=$cache_root/frontend-pnpm
      export FRONTEND_WORK_DIR=$cache_root/frontend-work/${source_id}.${BASHPID}
      export COMFY_FRONTEND_DIST_DIR=$output/current/dist
      export COMFY_FRONTEND_MODE=dist
      export FRONTEND_GIT_URL='' FRONTEND_GIT_REQUESTED_REF='' FRONTEND_GIT_REF=''
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

# Variant (frontend mode, Sage) -> Dockerfile target and image reference.
configure_variant() {
  local sage_mode=$1
  local sage variant target

  prepare_frontend_mode
  sage=true
  [[ "$sage_mode" != off ]] || sage=false

  variant=$COMFY_FRONTEND_MODE
  [[ "$sage" == false ]] || variant="${variant}-sage"
  case "$variant" in
    release|git) target=runtime ;;
    release-sage|git-sage|dist-sage) target=runtime-sage ;;
    dist) target=comfy-runtime ;;
    *) die 'invalid frontend/Sage image combination' ;;
  esac
  export COMFY_BUILD_TARGET=$target
  export LATENTCRATE_IMAGE="${LATENTCRATE_IMAGE:-latentcrate/comfy}:${variant}-${COMFYUI_REF}"
  export LATENTCRATE_SAGE=$sage_mode
  SAGE=$sage
}

build_local_frontend() {
  local engine=$1

  mkdir -p "$FRONTEND_OUTPUT_DIR" "$FRONTEND_PNPM_CACHE_DIR" "$FRONTEND_WORK_DIR"
  compose_tool "$engine" build frontend-fetch
  compose_tool "$engine" run --rm --no-deps frontend-fetch
  compose_tool "$engine" run --rm --no-deps frontend-build
  [[ -r "$COMFY_FRONTEND_DIST_DIR/index.html" ]] \
    || die 'frontend builder completed without publishing dist/index.html'
  rmdir -- "$FRONTEND_WORK_DIR" 2>/dev/null \
    || printf 'LatentCrate: temporary frontend files remain in %s; they are safe to remove after review\n' \
      "$FRONTEND_WORK_DIR" >&2
}
