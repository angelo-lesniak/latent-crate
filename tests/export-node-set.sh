#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
readonly PROJECT_ROOT
TEST_ROOT=$(mktemp -d)
readonly TEST_ROOT
trap 'rm -rf -- "$TEST_ROOT"' EXIT

custom_nodes="$TEST_ROOT/custom_nodes"
manifest="$TEST_ROOT/exported.toml"
report="$TEST_ROOT/exported.report"
mkdir -p \
  "$custom_nodes/.disabled" \
  "$custom_nodes/.HiddenNode" \
  "$custom_nodes/CleanNode" \
  "$custom_nodes/DirtyNode" \
  "$custom_nodes/Ignored.disabled" \
  "$custom_nodes/LocalNode" \
  "$custom_nodes/__pycache__"

make_checkout() {
  local path=$1 origin=$2
  git -c commit.gpgsign=false init -q "$path"
  git -C "$path" -c user.name='LatentCrate test' -c user.email='test@invalid' \
    -c commit.gpgsign=false commit --allow-empty -q -m fixture
  git -C "$path" remote add origin "$origin"
}

make_checkout "$custom_nodes/CleanNode" 'git@github.com:example/CleanNode.git'
make_checkout "$custom_nodes/DirtyNode" 'https://github.com/example/DirtyNode.git'
printf 'uncommitted\n' > "$custom_nodes/DirtyNode/local-change.txt"
printf 'local only\n' > "$custom_nodes/LocalNode/__init__.py"

bash "$PROJECT_ROOT/scripts/export-node-set.sh" "$custom_nodes" \
  > "$manifest" 2> "$report"

clean_commit=$(git -C "$custom_nodes/CleanNode" rev-parse HEAD)
grep -Fq 'name = "CleanNode"' "$manifest"
grep -Fq 'repository = "https://github.com/example/CleanNode"' "$manifest"
grep -Fq "commit = \"$clean_commit\"" "$manifest"
if grep -Fq 'DirtyNode' "$manifest" || grep -Fq 'LocalNode' "$manifest"; then
  printf 'export-node-set test: non-reproducible nodes entered the manifest\n' >&2
  exit 1
fi
grep -Fq 'Needs manual handling: DirtyNode' "$report"
grep -Fq 'Needs manual handling: LocalNode' "$report"
grep -Fq 'Needs manual handling: .HiddenNode' "$report"
if grep -Fq '.disabled' "$report" \
  || grep -Fq 'Ignored.disabled' "$report" \
  || grep -Fq '__pycache__' "$report"; then
  printf 'export-node-set test: ignored administrative directories were reported as manual work\n' >&2
  exit 1
fi
grep -Fq 'Exported 1 node(s); 3 need manual handling.' "$report"
python -c 'import pathlib, sys, tomllib; data = tomllib.loads(pathlib.Path(sys.argv[1]).read_text()); assert data["version"] == 1 and len(data["node"]) == 1' "$manifest"

printf 'LatentCrate node-set exporter checks passed.\n'
