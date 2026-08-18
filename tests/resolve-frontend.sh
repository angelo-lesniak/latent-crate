#!/usr/bin/env bash
# Engine-free tests for scripts/resolve-frontend.sh. The `ref` subcommand is
# exercised offline against the fake git in tests/fixtures/resolver-bin, which
# also records the hardened environment and the exact ls-remote invocation.
set -Eeuo pipefail

PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
readonly PROJECT_ROOT
RESOLVER="$PROJECT_ROOT/scripts/resolve-frontend.sh"
FAKE_GIT_DIR="$PROJECT_ROOT/tests/fixtures/resolver-bin"
chmod +x "$FAKE_GIT_DIR/git"

TEST_ROOT=$(mktemp -d)
trap 'rm -rf -- "$TEST_ROOT"' EXIT
RECORD_FILE="$TEST_ROOT/git-invocation"

REPOSITORY_URL=https://example.invalid/fork/frontend.git

resolver_output=
resolver_status=0
case_label=

run_resolver() {
  local mode=$1
  local ref=$2
  shift 2
  resolver_status=0
  resolver_output=$(PATH="$FAKE_GIT_DIR:$PATH" \
    FAKE_GIT_MODE="$mode" \
    FAKE_GIT_REF="$ref" \
    FAKE_GIT_RECORD_FILE="$RECORD_FILE" \
    bash "$RESOLVER" "$@" 2>&1) || resolver_status=$?
}

fail_case() {
  printf 'resolver test [%s]: %s\n' "$case_label" "$1" >&2
  printf '%s\n' "$resolver_output" >&2
  exit 1
}

expect_success_output() {
  local expected=$1
  ((resolver_status == 0)) || fail_case "expected exit 0, got $resolver_status"
  [[ "$resolver_output" == "$expected" ]] \
    || fail_case "expected output $expected"
}

expect_failure_output() {
  local expected=$1
  ((resolver_status != 0)) || fail_case 'expected a nonzero exit status'
  [[ "$resolver_output" == *"$expected"* ]] \
    || fail_case "expected output to contain: $expected"
}

# --- An exact commit passes through lowercased without invoking git at all
# --- (the fake git would fail hard in mode `fail`).
case_label='exact commit passthrough'
rm -f "$RECORD_FILE"
run_resolver fail unused ref "$REPOSITORY_URL" \
  AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABBBBBBBB
expect_success_output aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaabbbbbbbb
[[ ! -e "$RECORD_FILE" ]] || fail_case 'git must not run for an exact commit'

# --- A branch name resolves to the commit reported by ls-remote.
case_label='branch resolves to its commit'
run_resolver branch main ref "$REPOSITORY_URL" main
expect_success_output 1111111111111111111111111111111111111111

# --- The fake git recorded the hardened environment and the exact arguments.
case_label='hardened git invocation'
mapfile -t recorded < "$RECORD_FILE"
[[ "${recorded[0]}" == env=ok ]] \
  || fail_case "hardening environment was incomplete: ${recorded[0]}"
expected_arguments=(
  -c credential.helper=
  -c core.askPass=/bin/false
  -c http.followRedirects=false
  -C WORKDIR
  ls-remote
  "$REPOSITORY_URL"
  main
  refs/heads/main
  refs/tags/main
  'refs/tags/main^{}'
)
((${#recorded[@]} - 1 == ${#expected_arguments[@]})) \
  || fail_case "expected ${#expected_arguments[@]} git arguments, got $((${#recorded[@]} - 1))"
for index in "${!expected_arguments[@]}"; do
  actual=${recorded[index + 1]}
  expected=${expected_arguments[index]}
  if [[ "$expected" == WORKDIR ]]; then
    [[ -n "$actual" ]] || fail_case 'the -C working directory argument was empty'
    continue
  fi
  [[ "$actual" == "$expected" ]] \
    || fail_case "git argument $index was $actual, expected $expected"
done

# --- An annotated tag resolves to the peeled ^{} commit, not the tag object.
case_label='annotated tag resolves to the peeled commit'
run_resolver annotated-tag v1.0.0 ref "$REPOSITORY_URL" v1.0.0
expect_success_output 3333333333333333333333333333333333333333

# --- An unknown reference produces the not-found error.
case_label='reference not found'
run_resolver none v9.9.9 ref "$REPOSITORY_URL" v9.9.9
expect_failure_output 'frontend reference was not found: v9.9.9'

# --- An ls-remote failure produces the could-not-query error.
case_label='ls-remote failure'
run_resolver fail main ref "$REPOSITORY_URL" main
expect_failure_output "could not query frontend reference main from $REPOSITORY_URL"

# --- The target and tag subcommands reject an unsupported variant.
case_label='unsupported target variant'
run_resolver fail unused target bogus
expect_failure_output 'Unsupported variant: bogus'

case_label='unsupported tag variant'
run_resolver fail unused tag current bogus
expect_failure_output 'Unsupported variant: bogus'

printf 'LatentCrate frontend resolver checks passed.\n'
