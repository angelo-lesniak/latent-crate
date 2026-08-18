#!/usr/bin/env bash
set -Eeuo pipefail

# Single source of truth for the frontend/Sage variant -> Dockerfile target and
# image tag mapping, and for resolving a frontend Git reference to an exact
# commit. Both lib/latentcrate/frontend.sh and .github/workflows/build.yml call
# this script instead of keeping their own copies.

die() {
  printf 'LatentCrate: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat >&2 <<'EOF'
usage:
  resolve-frontend.sh target <release|release-sage|frontend-git|frontend-git-sage>
  resolve-frontend.sh tag <profile> <release|release-sage|frontend-git|frontend-git-sage>
  resolve-frontend.sh ref <https-repository-url> <commit-branch-tag-or-pr-reference>
EOF
  exit 2
}

# Canonical Bash copy of the Git config/env hardening options. The Python copy
# lives in scripts/manage-node-set.py and the Dockerfile copy in
# services/comfy/Dockerfile (frontend-git-builder); keep them aligned.
hardened_git() {
  GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/false \
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    git -c credential.helper= -c core.askPass=/bin/false \
    -c http.followRedirects=false "$@"
}

variant_target() {
  case "$1" in
    release) printf 'runtime\n' ;;
    release-sage) printf 'runtime-sage\n' ;;
    frontend-git) printf 'runtime-frontend-git\n' ;;
    frontend-git-sage) printf 'runtime-frontend-git-sage\n' ;;
    *) printf 'Unsupported variant: %s\n' "$1" >&2; exit 1 ;;
  esac
}

variant_tag() {
  local profile=$1
  local variant=$2

  case "$variant" in
    release) printf '%s\n' "$profile" ;;
    release-sage) printf '%s-sage\n' "$profile" ;;
    frontend-git) printf '%s-frontend-git\n' "$profile" ;;
    frontend-git-sage) printf '%s-frontend-git-sage\n' "$profile" ;;
    *) printf 'Unsupported variant: %s\n' "$variant" >&2; exit 1 ;;
  esac
}

resolve_ref() {
  local url=$1
  local requested=$2
  local git_workdir listing peeled resolved status

  [[ "$url" =~ ^https://[^/@[:space:]]+/[^?#[:space:]]+$ ]] \
    || die 'frontend Git URL must use HTTPS; credentials in URLs are not allowed'
  [[ -n "$requested" ]] || die 'frontend Git mode requires a commit, branch, tag, or pull-request reference'
  [[ "$requested" != -* && ! "$requested" =~ [[:space:]] ]] \
    || die 'frontend Git references must not begin with a dash or contain whitespace'

  if [[ "$requested" =~ ^[0-9a-fA-F]{40}$ ]]; then
    printf '%s\n' "${requested,,}"
    return
  fi

  command -v git >/dev/null 2>&1 \
    || die 'git is required to resolve a frontend branch, tag, or pull-request reference'
  git_workdir=$(mktemp -d) || die 'could not create a temporary Git configuration boundary'
  status=0
  listing=$(hardened_git -C "$git_workdir" ls-remote "$url" \
      "$requested" \
      "refs/heads/$requested" \
      "refs/tags/$requested" \
      "refs/tags/$requested^{}" 2>/dev/null) || status=$?
  rm -rf -- "$git_workdir"
  if ((status != 0)); then
    die "could not query frontend reference $requested from $url"
  fi
  [[ -n "$listing" ]] || die "frontend reference was not found: $requested"
  peeled=$(awk '$2 ~ /\^\{\}$/ {print $1; exit}' <<< "$listing")
  resolved=${peeled:-$(awk 'NR == 1 {print $1}' <<< "$listing")}
  [[ "$resolved" =~ ^[0-9a-fA-F]{40}$ ]] || die "frontend reference did not resolve to a commit: $requested"
  printf '%s\n' "${resolved,,}"
}

case "${1:-}" in
  target)
    (($# == 2)) || usage
    variant_target "$2"
    ;;
  tag)
    (($# == 3)) || usage
    variant_tag "$2" "$3"
    ;;
  ref)
    (($# == 3)) || usage
    resolve_ref "$2" "$3"
    ;;
  *) usage ;;
esac
