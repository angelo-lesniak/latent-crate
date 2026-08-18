#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: bash export-node-set.sh /path/to/ComfyUI/custom_nodes > my-nodes.toml

Writes a LatentCrate node-set manifest to stdout. Progress and nodes that need
manual handling are written to stderr.
EOF
}

die() {
  printf 'LatentCrate node-set exporter: %s\n' "$*" >&2
  exit 1
}

if ((BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 4))); then
  die "bash 4.4 or newer is required (found $BASH_VERSION)"
fi

[[ $# == 1 ]] || { usage; exit 2; }
command -v git >/dev/null 2>&1 || die 'git is required'
[[ -d "$1" ]] || die "custom_nodes directory does not exist: $1"

custom_nodes_root=$(cd -- "$1" && pwd -P)
readonly custom_nodes_root

# Do not consult user/system credential helpers or prompt while inspecting a
# checkout. Local execution-capable Git settings are rejected below.
export GIT_ASKPASS=/bin/false
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_NOSYSTEM=1
export GIT_OPTIONAL_LOCKS=0
export GIT_TERMINAL_PROMPT=0

declare -A seen_names=()
declare -a manifest_entries=()
included=0
skipped=0

report_skip() {
  local name=$1 reason=$2
  printf 'Needs manual handling: %s — %s\n' "$name" "$reason" >&2
  ((skipped += 1))
}

git_in_node() {
  local node=$1
  shift
  git \
    -c credential.helper= \
    -c core.askPass=/bin/false \
    -c core.fsmonitor=false \
    -c core.hooksPath=/dev/null \
    -c core.untrackedCache=false \
    -c http.followRedirects=false \
    --git-dir="$node/.git" \
    --work-tree="$node" \
    "$@"
}

has_unsafe_local_git_config() {
  local node=$1 output status
  if output=$(git \
      --git-dir="$node/.git" \
      config --local --no-includes --name-only --get-regexp \
      '^(filter\.|diff\..*\.(command|textconv)$|core\.(fsmonitor|hookspath|attributesfile|sshcommand)$|include\.|includeif\.|url\.|credential\.|http\.)' \
      2>/dev/null); then
    [[ -n "$output" ]]
    return
  else
    status=$?
  fi
  ((status != 1))
}

normalize_github_origin() {
  local origin=$1 owner repository

  if [[ "$origin" =~ ^https://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)$ ]]; then
    owner=${BASH_REMATCH[1]}
    repository=${BASH_REMATCH[2]}
  elif [[ "$origin" =~ ^git@github\.com:([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)$ ]]; then
    owner=${BASH_REMATCH[1]}
    repository=${BASH_REMATCH[2]}
  elif [[ "$origin" =~ ^ssh://git@github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)$ ]]; then
    owner=${BASH_REMATCH[1]}
    repository=${BASH_REMATCH[2]}
  else
    return 1
  fi

  repository=${repository%.git}
  [[ -n "$owner" && -n "$repository" ]] || return 1
  printf 'https://github.com/%s/%s\n' "$owner" "$repository"
}

while IFS= read -r -d '' node; do
  name=${node##*/}

  if [[ "$name" == .disabled || "$name" == __pycache__ || "$name" == *.disabled ]]; then
    continue
  fi
  if [[ "$name" == .* ]]; then
    report_skip "$name" 'hidden custom-node directories are not supported by LatentCrate'
    continue
  fi
  if [[ ! "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    report_skip "$name" 'directory name is not valid in a LatentCrate node set'
    continue
  fi
  folded_name=${name,,}
  if [[ -n "${seen_names[$folded_name]:-}" ]]; then
    report_skip "$name" 'another node has the same name when case is ignored'
    continue
  fi
  seen_names[$folded_name]=1

  if [[ -L "$node" ]]; then
    report_skip "$name" 'top-level symbolic links cannot be exported'
    continue
  fi
  if [[ ! -d "$node" || ! -d "$node/.git" || -L "$node/.git" ]]; then
    report_skip "$name" 'not a regular Git checkout; copy it through COMFY_LOCAL_NODES_DIR instead'
    continue
  fi
  if has_unsafe_local_git_config "$node"; then
    report_skip "$name" 'checkout has execution-capable or credential-related local Git configuration'
    continue
  fi

  if ! origin=$(git_in_node "$node" remote get-url origin 2>/dev/null); then
    report_skip "$name" 'Git origin is missing or unreadable'
    continue
  fi
  if ! repository=$(normalize_github_origin "$origin"); then
    report_skip "$name" 'origin is not a public, credential-free GitHub repository'
    continue
  fi
  if ! commit=$(git_in_node "$node" rev-parse --verify HEAD 2>/dev/null); then
    report_skip "$name" 'HEAD commit is unreadable'
    continue
  fi
  commit=${commit,,}
  if [[ ! "$commit" =~ ^[0-9a-f]{40}$ ]]; then
    report_skip "$name" 'HEAD is not a full 40-character commit'
    continue
  fi
  if ! changes=$(git_in_node "$node" status --porcelain --untracked-files=all --ignore-submodules=all 2>/dev/null); then
    report_skip "$name" 'working-tree state could not be inspected safely'
    continue
  fi
  if [[ -n "$changes" ]]; then
    report_skip "$name" "working tree is dirty at $commit; uncommitted changes cannot be pinned"
    continue
  fi

  manifest_entries+=("[[node]]
name = \"$name\"
repository = \"$repository\"
commit = \"$commit\"")
  ((included += 1))
done < <(find "$custom_nodes_root" -mindepth 1 -maxdepth 1 -print0 | LC_ALL=C sort -z)

((included > 0)) || die 'no clean public GitHub node checkouts could be exported'

printf 'version = 1\n'
printf 'description = "Third-party nodes exported from an existing ComfyUI installation"\n'
for entry in "${manifest_entries[@]}"; do
  printf '\n%s\n' "$entry"
done

printf 'Exported %d node(s); %d need manual handling. Review every commit before sharing or installing the set.\n' \
  "$included" "$skipped" >&2
