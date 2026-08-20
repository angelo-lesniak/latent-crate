# shellcheck shell=bash

[[ -n "${PROJECT_ROOT:-}" ]] \
  || { printf 'LatentCrate: PROJECT_ROOT must be set before loading templates.sh\n' >&2; return 1; }

validate_template_name() {
  local name=$1
  [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || die "unsafe template name: $name"
}

validate_draft_name() {
  local name=$1
  [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && "$name" != all ]] \
    || die "unsafe model-set name: $name"
}

run_template_tool() {
  local action=$1
  local profile=$2
  local template=${3:-}
  local name=${4:-}
  local engine

  profile_file "$profile" >/dev/null
  # Template packages follow the backend profile and do not depend on a
  # frontend fork or local frontend build. Avoid unrelated Git resolution.
  export COMFY_FRONTEND_MODE=release
  set_default_sage_variant
  configure_variant "$profile" "$SAGE_MODE"
  engine=$(detect_engine)

  # Build the selected runtime first: the helper intentionally reads the
  # template package installed in that exact image, never a moving web index.
  compose "$engine" "$profile" build comfy
  if [[ "$action" == list ]]; then
    compose_tool "$engine" "$profile" run --rm --no-deps -T template-inspector list
    return
  fi

  [[ "$action" == create-model-set ]] \
    || die "unsupported template action: $action"
  validate_template_name "$template"
  validate_draft_name "$name"
  export TEMPLATE_DRAFT_OUTPUT_DIR=$PROJECT_ROOT/build/model-set-drafts
  mkdir -p "$TEMPLATE_DRAFT_OUTPUT_DIR"
  compose_tool "$engine" "$profile" run --rm --no-deps -T template-draft \
    create-model-set "$template" --name "$name"
}
