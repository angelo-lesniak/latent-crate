#!/usr/bin/env bash
# The static gate (no container engine, no GPU, no model): syntax and lint for
# the shell files, Ruff for the package and the tests, the unit tests (the
# repository contracts and the entrypoint policy among them), the
# podman-compose parser, the host-path scan, and a Compose render with every
# installed engine.
set -Eeuo pipefail

PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
readonly PROJECT_ROOT
cd "$PROJECT_ROOT"

python_bin=python3
command -v python3 >/dev/null 2>&1 || python_bin=python
if ! command -v "$python_bin" >/dev/null 2>&1; then
  printf 'static: required command is unavailable: python3\n' >&2
  exit 1
fi

# LATENTCRATE_STATIC_STRICT=1 turns every optional-tool skip below into a
# hard failure. CI sets it so a missing tool cannot silently narrow the gate.
skip() {
  if [[ "${LATENTCRATE_STATIC_STRICT:-0}" == 1 ]]; then
    printf 'static: STRICT: %s\n' "$1" >&2
    exit 1
  fi
  printf 'SKIPPED: %s\n' "$1"
}

shell_files=(
  bin/latentcrate
  services/comfy/*.sh
  tests/podman-compose.sh
  tests/static.sh
  tests/fixtures/podman-compose-bin/podman
  tests/fixtures/git-bin/git
  tests/fixtures/engine-bin/nvidia-smi
)
for file in "${shell_files[@]}"; do
  bash -n "$file"
done
# Each module imports on its own, subpackages included: the unit tests
# import them in one order, which can hide an import cycle that another
# entry point runs into.
while IFS= read -r file; do
  module=${file%.py}
  module=${module%/__init__}
  module=${module//\//.}
  [[ "$module" == *.__main__ ]] || "$python_bin" -c "import $module"
done < <(find latentcrate -name '*.py' | sort)
# unittest reports a skipped test in its summary line and still exits 0;
# strict mode treats the skip like a missing tool.
unittest_log=$(mktemp)
trap 'rm -f -- "$unittest_log"' EXIT
"$python_bin" -m unittest discover -s tests -p 'test_*.py' 2>&1 | tee "$unittest_log" >&2
unittest_summary=$(tail -n 1 "$unittest_log")
[[ "$unittest_summary" != *skipped=* ]] || skip "unit tests: $unittest_summary"


if command -v ruff >/dev/null 2>&1; then
  # Every Python file in the tree (.gitignore honoured) and the fake
  # engine, which has no extension.
  ruff check . tests/fixtures/engine-bin/podman
  ruff format --check . tests/fixtures/engine-bin/podman
else
  skip 'Ruff lint and format check (ruff not found)'
fi

compose_validated=false
if command -v podman-compose >/dev/null 2>&1; then
  # Renders every file combination with the pinned podman-compose parser
  # and checks the container arguments; the engine renders below are the
  # other parsers.
  bash tests/podman-compose.sh
  compose_validated=true
else
  skip 'podman-compose 1.6 compatibility (podman-compose not found)'
fi

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck "${shell_files[@]}"
else
  skip 'ShellCheck lint (shellcheck not found; syntax checks only)'
fi

if command -v rg >/dev/null 2>&1; then
  # No tracked or new file may name a machine-specific host path. The port
  # plan describes this scan and the host; it leaves with the plan. Each
  # match is printed on its own line so that a line can hold both. The
  # first alternative is spelled with a class so it does not match this
  # script. Without a work tree (a `git archive` extract) the whole
  # directory is scanned.
  scanned=()
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    # A path deleted in the work tree but still in the index is not a file.
    while IFS= read -r -d '' file; do
      [[ -f "$file" ]] && scanned+=("$file")
    done < <(git ls-files -z --cached --others --exclude-standard)
  else
    scanned=(.)
  fi
  rg_status=0
  matches=$(rg -n -o '/mnt/host[s]hare|(^|[^/A-Za-z0-9._-])/home/[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+' \
    -- "${scanned[@]}") || rg_status=$?
  if ((rg_status > 1)); then
    printf 'static: the host path scan failed (rg exit %s)\n' "$rg_status" >&2
    exit 1
  fi
  if printf '%s\n' "$matches" | grep -Ev '^$|^BACKPORT_REVISED\.md:'; then
    printf 'static: machine-specific absolute host paths remain\n' >&2
    exit 1
  fi
else
  skip 'absolute host path scan (rg not found)'
fi

# The Compose files require every setting and the values the wrapper
# derives on each call (compose.yaml, the interpolation note); the render
# exports the documented defaults from the reference, which the package
# keeps literal.
set -a
# shellcheck source=/dev/null
. ./.env.example
set +a
HOST_UID=$(id -u)
HOST_GID=$(id -g)
export HOST_UID HOST_GID HOST_MODEL_GID="$HOST_GID"
export FRONTEND_GIT_URL=https://github.com/Comfy-Org/ComfyUI_frontend.git
export FRONTEND_GIT_REF=0000000000000000000000000000000000000000
export FRONTEND_GIT_REQUESTED_REF=fixture

# Shown only on failure: podman-compose warns about every unused network.
render_compose() {
  local engine=$1
  shift
  local output
  output=$("$engine" compose "$@" --env-file pins.env config 2>&1 >/dev/null) \
    || { printf '%s\n' "$output" >&2; return 1; }
}

# The file combinations the package passes (engine.Engine.compose): the
# service with the engine overlay, the GPU device request unless the
# command runs without a GPU, and the dist-mode frontend mount; each with
# the variant's image, target, and container frontend mode.
render_compose_variants() {
  local engine=$1
  local -a files=(--file compose.yaml --file "compose.${engine}.yaml")
  local -a gpu=("${files[@]}" --file compose.gpu.yaml)
  LATENTCRATE_IMAGE=latentcrate/comfy:release-fixture COMFY_BUILD_TARGET=runtime COMFY_FRONTEND_MODE=git \
    render_compose "$engine" "${gpu[@]}"
  LATENTCRATE_IMAGE=latentcrate/comfy:release-sage-fixture COMFY_BUILD_TARGET=runtime-sage COMFY_FRONTEND_MODE=git \
    LATENTCRATE_SAGE=available render_compose "$engine" "${gpu[@]}"
  LATENTCRATE_IMAGE=latentcrate/comfy:dist-fixture COMFY_BUILD_TARGET=comfy-runtime COMFY_FRONTEND_MODE=dist \
    COMFY_FRONTEND_DIST_DIR="$PROJECT_ROOT/tests/fixtures/frontend-dist" \
    render_compose "$engine" "${gpu[@]}" --file compose.frontend-dist.yaml
  LATENTCRATE_IMAGE=latentcrate/comfy:release-fixture COMFY_BUILD_TARGET=runtime COMFY_FRONTEND_MODE=git \
    render_compose "$engine" "${files[@]}"
}

for engine in podman docker; do
  if command -v "$engine" >/dev/null 2>&1 && "$engine" compose version >/dev/null 2>&1; then
    compose_validated=true
    render_compose_variants "$engine"
  fi
done

if [[ "$compose_validated" != true ]]; then
  skip 'compose validation (neither podman-compose nor an engine with Compose found)'
fi

printf 'LatentCrate static checks passed.\n'
