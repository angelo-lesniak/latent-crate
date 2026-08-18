#!/usr/bin/env bash
set -Eeuo pipefail

readonly SOURCE_ROOT=/source
readonly WORK_ROOT=/work/source
readonly OUTPUT_ROOT=/output
readonly STORE_ROOT=/pnpm/store

# Apply the profile's pnpm choice to commands started by package lifecycle
# scripts too. A per-command CLI option is not inherited by nested pnpm calls.
export PNPM_CONFIG_PM_ON_FAIL=ignore

die() {
  printf 'LatentCrate frontend builder: %s\n' "$*" >&2
  exit 1
}

clean_work() {
  rm -rf -- "$WORK_ROOT" "$HOME" "$XDG_CACHE_HOME"
}

copy_source() {
  # A failed earlier build may have left files in the host-cache work mount.
  # Start each networked/offline phase from an empty tool home and source tree.
  clean_work
  mkdir -p "$WORK_ROOT" "$HOME" "$XDG_CACHE_HOME"
  (
    cd "$SOURCE_ROOT"
    tar \
      --exclude='./.git' \
      --exclude='*/.git' \
      --exclude='./node_modules' \
      --exclude='*/node_modules' \
      --exclude='./dist' \
      --exclude='./.pnpm-store' \
      --exclude='*/.pnpm-store' \
      -cf - .
  ) | tar -C "$WORK_ROOT" -xf -
}

source_tree_digest() {
  (
    cd "$WORK_ROOT"
    LC_ALL=C find . \
      -type d \( -name .git -o -name node_modules -o -name dist -o -name .pnpm-store \) -prune \
      -o \( -type f -o -type l \) -print0 \
      | LC_ALL=C sort -z \
      | while IFS= read -r -d '' item; do
          if [[ -L "$item" ]]; then
            printf 'link\0%s\0%s\0' "$item" "$(readlink -- "$item")"
          else
            printf 'file\0%s\0' "$item"
            sha256sum "$item" | cut -d ' ' -f 1
          fi
        done \
      | sha256sum \
      | cut -d ' ' -f 1
  )
}

publish_dist() {
  local source_digest=$1
  local content_digest staging backup

  [[ -r "$WORK_ROOT/dist/index.html" ]] \
    || die 'the production build did not create a readable dist/index.html'
  if find "$WORK_ROOT/dist" -type l -print -quit | grep -q .; then
    die 'the generated dist tree contains a symbolic link'
  fi

  mkdir -p "$OUTPUT_ROOT"
  # The lock file intentionally stays behind after publishing: removing a file
  # that another process may have opened for flock would break the exclusion.
  exec 9> "$OUTPUT_ROOT/.build.lock"
  if ! flock -n 9; then
    die "another build appears to be publishing into $OUTPUT_ROOT"
  fi

  staging="$OUTPUT_ROOT/.current.staging.$$"
  backup="$OUTPUT_ROOT/.current.backup"
  cleanup() {
    rm -rf -- "$staging"
  }
  trap cleanup EXIT

  if [[ ! -e "$OUTPUT_ROOT/current" && -d "$backup" ]]; then
    mv "$backup" "$OUTPUT_ROOT/current"
  elif [[ -e "$OUTPUT_ROOT/current" ]]; then
    rm -rf -- "$backup"
  fi
  for stale_staging in "$OUTPUT_ROOT"/.current.staging.*; do
    [[ -e "$stale_staging" ]] || continue
    rm -rf -- "$stale_staging"
  done
  rm -rf -- "$staging"
  mkdir -p "$staging/dist" "$staging/build-info"
  cp -a "$WORK_ROOT/dist/." "$staging/dist/"
  content_digest=$(
    cd "$staging/dist"
    LC_ALL=C find . -type f -print0 \
      | LC_ALL=C sort -z \
      | xargs -0 sha256sum \
      | sha256sum \
      | cut -d ' ' -f 1
  )
  printf '%s\n' "$source_digest" > "$staging/build-info/frontend.source.sha256"
  printf '%s\n' "$content_digest" > "$staging/build-info/frontend.content.sha256"
  node --version > "$staging/build-info/frontend.node-version"
  pnpm --version > "$staging/build-info/frontend.pnpm-version"

  if [[ -e "$OUTPUT_ROOT/current" ]]; then
    mv "$OUTPUT_ROOT/current" "$backup"
  fi
  if mv "$staging" "$OUTPUT_ROOT/current"; then
    rm -rf -- "$backup"
  else
    [[ ! -e "$OUTPUT_ROOT/current" && -e "$backup" ]] \
      && mv "$backup" "$OUTPUT_ROOT/current"
    die 'could not publish the completed frontend build'
  fi

  printf 'Built local frontend assets: %s\n' "$OUTPUT_ROOT/current/dist"
  printf 'Frontend source digest: %s\n' "$source_digest"
  printf 'Frontend content digest: %s\n' "$content_digest"
  cleanup
  trap - EXIT
  flock -u 9
  exec 9>&-
}

mode=${1:-}
[[ -r "$SOURCE_ROOT/package.json" ]] || die 'source package.json is missing or unreadable'
[[ -r "$SOURCE_ROOT/pnpm-lock.yaml" ]] || die 'source pnpm-lock.yaml is missing or unreadable'
mkdir -p "$STORE_ROOT"

case "$mode" in
  fetch)
    copy_source
    cd "$WORK_ROOT"
    # Populate package content and registry metadata without running dependency
    # or project lifecycle scripts in the networked helper. pm-on-fail=ignore
    # keeps the version-profile pnpm binary instead of downloading a different
    # packageManager version that would not exist in the offline helper.
    pnpm --config.pm-on-fail=ignore install \
      --frozen-lockfile \
      --ignore-scripts \
      --store-dir "$STORE_ROOT"
    cd /
    clean_work
    ;;
  build)
    copy_source
    source_digest=$(source_tree_digest)
    cd "$WORK_ROOT"
    pnpm --config.pm-on-fail=ignore install \
      --offline \
      --frozen-lockfile \
      --trust-lockfile \
      --store-dir "$STORE_ROOT"
    pnpm build
    publish_dist "$source_digest"
    cd /
    clean_work
    ;;
  *) die 'expected fetch or build' ;;
esac
