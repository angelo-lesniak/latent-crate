#!/usr/bin/env bash
set -Eeuo pipefail

# This file uses mapfile -d (bash 4.4+). macOS ships bash 3.2, which fails in
# confusing ways; mirror the guard at the top of bin/latentcrate.
if ((BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 4))); then
  printf 'static: bash %s is too old; bash 4.4 or newer is required. Run the checks inside WSL2 or native Linux.\n' \
    "$BASH_VERSION" >&2
  exit 1
fi

PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
readonly PROJECT_ROOT
cd "$PROJECT_ROOT"
export CUSTOM_NODE_CACHE_KEY=static-fixture

if ! command -v python >/dev/null 2>&1; then
  printf 'static: required command is unavailable: python\n' >&2
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

# Fixture fakes are extensionless commands, so the find sweeps below cannot
# discover them; list them explicitly.
fixture_shell_files=(
  tests/fixtures/fake-bin/docker
  tests/fixtures/fake-bin/flock
  tests/fixtures/podman-compose-bin/podman
  tests/fixtures/resolver-bin/git
)
mapfile -d '' doctor_fixture_files < <(find tests/fixtures/doctor-bin -type f -print0)
fixture_shell_files+=("${doctor_fixture_files[@]}")

shell_files=(bin/latentcrate "${fixture_shell_files[@]}")
mapfile -d '' implementation_shell_files < <(find lib -type f -name '*.sh' -print0)
mapfile -d '' executable_shell_files < <(find scripts services tests -type f -name '*.sh' -print0)
shell_files+=("${implementation_shell_files[@]}" "${executable_shell_files[@]}")
bash -n "${shell_files[@]}"

while IFS= read -r -d '' python_file; do
  python -c 'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text())' "$python_file"
done < <(find scripts services tests -type f -name '*.py' -print0)

python -m unittest discover -s tests -p 'test_*.py'
python tests/check-project.py
bash tests/cli.sh
bash tests/node-deps-lifecycle.sh
bash tests/entrypoint.sh
bash tests/doctor.sh
bash tests/resolve-frontend.sh
bash tests/export-node-set.sh

if command -v podman-compose >/dev/null 2>&1; then
  bash tests/podman-compose.sh
else
  skip 'podman-compose 1.6 compatibility (podman-compose not found)'
fi

if command -v shellcheck >/dev/null 2>&1; then
  # The executable is the analysis root for its fixed sourced modules. Passing
  # the modules again as standalone scripts would create false unused-global
  # reports because their consumers live in the dispatcher.
  shellcheck -x bin/latentcrate "${fixture_shell_files[@]}" \
    "${executable_shell_files[@]}"
else
  skip 'ShellCheck lint (shellcheck not found; syntax checks only)'
fi

if command -v rg >/dev/null 2>&1; then
  if rg -n '/mnt/|/home/[A-Za-z0-9._-]+' \
    .env.example compose*.yaml config scripts services versions; then
    printf 'static: machine-specific absolute host paths remain\n' >&2
    exit 1
  fi
else
  skip 'absolute host path scan (rg not found)'
fi

compose_validated=false
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  compose_validated=true
  for profile in current edge; do
    docker compose \
      --file compose.yaml \
      --file compose.docker.yaml \
      --env-file "versions/${profile}.env" \
      config >/dev/null
  done
  COMFY_FRONTEND_MODE=git \
  COMFY_BUILD_TARGET=runtime-frontend-git \
  FRONTEND_GIT_URL=https://github.com/Comfy-Org/ComfyUI_frontend.git \
  FRONTEND_GIT_REF=0000000000000000000000000000000000000000 \
  FRONTEND_GIT_REQUESTED_REF=fixture \
    docker compose \
      --file compose.yaml \
      --file compose.docker.yaml \
      --env-file versions/current.env \
      config >/dev/null
  COMFY_FRONTEND_MODE=dist \
  COMFY_FRONTEND_DIST_DIR="$PROJECT_ROOT/tests/fixtures/frontend-dist" \
    docker compose \
      --file compose.yaml \
      --file compose.docker.yaml \
      --file compose.frontend-dist.yaml \
      --env-file versions/current.env \
      config >/dev/null
  FRONTEND_SOURCE_DIR="$PROJECT_ROOT/tests/fixtures/frontend-source" \
  FRONTEND_OUTPUT_DIR="$PROJECT_ROOT/data/cache/frontend-builds/test" \
  FRONTEND_PNPM_CACHE_DIR="$PROJECT_ROOT/data/cache/frontend-pnpm" \
  FRONTEND_WORK_DIR="$PROJECT_ROOT/data/cache/frontend-work/test" \
  NODE_DEPS_SOURCE_DIR="$PROJECT_ROOT/tests/fixtures" \
  NODE_DEPS_LOCAL_SOURCE_DIR="$PROJECT_ROOT/tests/fixtures/custom-nodes" \
  NODE_DEPS_OUTPUT_DIR="$PROJECT_ROOT/build" \
    docker compose \
      --file compose.yaml \
      --file compose.docker.yaml \
      --env-file versions/current.env \
      --profile tools \
      config >/dev/null
fi

if command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
  compose_validated=true
  for profile in current edge; do
    podman compose \
      --file compose.yaml \
      --file compose.podman.yaml \
      --env-file "versions/${profile}.env" \
      config >/dev/null
  done
  COMFY_FRONTEND_MODE=git \
  COMFY_BUILD_TARGET=runtime-frontend-git \
  FRONTEND_GIT_URL=https://github.com/Comfy-Org/ComfyUI_frontend.git \
  FRONTEND_GIT_REF=0000000000000000000000000000000000000000 \
  FRONTEND_GIT_REQUESTED_REF=fixture \
    podman compose \
      --file compose.yaml \
      --file compose.podman.yaml \
      --env-file versions/current.env \
      config >/dev/null
  COMFY_FRONTEND_MODE=dist \
  COMFY_FRONTEND_DIST_DIR="$PROJECT_ROOT/tests/fixtures/frontend-dist" \
    podman compose \
      --file compose.yaml \
      --file compose.podman.yaml \
      --file compose.frontend-dist.yaml \
      --env-file versions/current.env \
      config >/dev/null
  FRONTEND_SOURCE_DIR="$PROJECT_ROOT/tests/fixtures/frontend-source" \
  FRONTEND_OUTPUT_DIR="$PROJECT_ROOT/data/cache/frontend-builds/test" \
  FRONTEND_PNPM_CACHE_DIR="$PROJECT_ROOT/data/cache/frontend-pnpm" \
  FRONTEND_WORK_DIR="$PROJECT_ROOT/data/cache/frontend-work/test" \
  NODE_DEPS_SOURCE_DIR="$PROJECT_ROOT/tests/fixtures" \
  NODE_DEPS_LOCAL_SOURCE_DIR="$PROJECT_ROOT/tests/fixtures/custom-nodes" \
  NODE_DEPS_OUTPUT_DIR="$PROJECT_ROOT/build" \
    podman compose \
      --file compose.yaml \
      --file compose.podman.yaml \
      --env-file versions/current.env \
      --profile tools \
      config >/dev/null
fi

if [[ "$compose_validated" != true ]]; then
  skip 'compose validation (no container engine with Compose found)'
fi

printf 'LatentCrate static checks passed.\n'
