# shellcheck shell=bash

running_container_id() {
  local engine=$1
  local container_id
  container_id=$(running_service_container_ids "$engine" comfy)
  [[ -n "$container_id" ]] || die 'the ComfyUI container is not running'
  [[ "$container_id" != *$'\n'* ]] \
    || die 'multiple running ComfyUI containers were found for this Compose project'
  printf '%s\n' "$container_id"
}

assert_running_image() {
  local engine=$1
  local container_id image expected_id actual_id

  container_id=$(running_container_id "$engine")
  image=$LATENTCRATE_IMAGE
  expected_id=$("$engine" image inspect --format '{{.Id}}' "$image" 2>/dev/null) \
    || die "selected image does not exist locally: $image"
  actual_id=$("$engine" inspect --format '{{.Image}}' "$container_id" 2>/dev/null) \
    || die "could not inspect running container: $container_id"
  expected_id=${expected_id#sha256:}
  actual_id=${actual_id#sha256:}
  if [[ "$actual_id" != "$expected_id" ]]; then
    die 'running container uses a different image; rerun the same latentcrate up command used for this frontend/Sage selection'
  fi
}

wait_until_healthy() {
  local engine=$1
  local timeout=$2
  local container_id status deadline

  container_id=$(running_container_id "$engine")
  deadline=$((SECONDS + timeout))
  while ((SECONDS < deadline)); do
    status=$("$engine" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || printf unknown)
    case "$status" in
      healthy)
        printf 'LatentCrate is healthy at http://%s:%s\n' \
          "${COMFY_BIND_ADDRESS:-127.0.0.1}" "${COMFY_PORT:-4207}"
        return
        ;;
      unhealthy|exited|dead)
        die "container became $status before it was ready. Check the logs: bash bin/latentcrate logs"
        ;;
    esac
    sleep 2
  done
  die "container did not become healthy within ${timeout}s. Check the logs: bash bin/latentcrate logs"
}

run_gpu_smoke() {
  local engine=$1
  local sage=$2
  local report expected_frontend_content expected_sage smoke_status
  local -a smoke_env

  if [[ "$FRONTEND_SOURCE_BUILD" == true && ! -r "$COMFY_FRONTEND_DIST_DIR/index.html" ]]; then
    die 'no generated frontend exists for this source; rerun the matching latentcrate up --frontend-source command'
  fi

  assert_running_image "$engine"
  expected_sage=0
  [[ "$sage" == true ]] && expected_sage=1
  smoke_env=(
    env
    "LATENTCRATE_EXPECT_COMFYUI_REF=${COMFYUI_REF}"
    "LATENTCRATE_EXPECT_CUSTOM_NODE_CUDA_ARCH_LIST=${COMFY_CUDA_ARCH_LIST}"
    "LATENTCRATE_EXPECT_FFMPEG_REF=${FFMPEG_REF}"
    "LATENTCRATE_EXPECT_NV_CODEC_HEADERS_REF=${NV_CODEC_HEADERS_REF}"
    "LATENTCRATE_EXPECT_PYTORCH_DEVEL_IMAGE=${PYTORCH_DEVEL_TAG}"
    "LATENTCRATE_EXPECT_PYTORCH_RUNTIME_IMAGE=${PYTORCH_RUNTIME_TAG}"
    "LATENTCRATE_EXPECT_SAGE=${expected_sage}"
    "LATENTCRATE_EXPECT_SVT_AV1_REF=${SVT_AV1_REF}"
    "LATENTCRATE_EXPECT_TORCHCODEC_VERSION=${TORCHCODEC_VERSION}"
  )
  case "$COMFY_FRONTEND_MODE" in
    release|git)
      smoke_env+=(
        "LATENTCRATE_EXPECT_FRONTEND_MODE=git"
        "LATENTCRATE_EXPECT_FRONTEND_GIT_URL=${FRONTEND_GIT_URL}"
        "LATENTCRATE_EXPECT_FRONTEND_COMMIT=${FRONTEND_GIT_REF}"
        "LATENTCRATE_EXPECT_FRONTEND_NODE_IMAGE=${FRONTEND_NODE_TAG}"
        "LATENTCRATE_EXPECT_FRONTEND_PNPM_VERSION=${FRONTEND_PNPM_VERSION}"
      )
      ;;
    dist)
      expected_frontend_content=$(frontend_tree_digest "$COMFY_FRONTEND_DIST_DIR")
      smoke_env+=(
        "LATENTCRATE_EXPECT_FRONTEND_MODE=dist"
        "LATENTCRATE_EXPECT_FRONTEND_CONTENT_SHA256=${expected_frontend_content}"
      )
      ;;
  esac
  if [[ "$sage" == true ]]; then
    smoke_env+=(
      "LATENTCRATE_EXPECT_SAGEATTENTION_REF=${SAGEATTENTION_REF}"
      "LATENTCRATE_EXPECT_SAGE_CUDA_ARCH_LIST=${COMFY_CUDA_ARCH_LIST}"
    )
  fi
  mkdir -p "$PROJECT_ROOT/reports"
  report="$PROJECT_ROOT/reports/gpu-${LATENTCRATE_IMAGE##*:}-$(date +%Y%m%d-%H%M%S).log"
  smoke_status=0
  if [[ "$sage" == true ]]; then
    compose "$engine" exec comfy \
      "${smoke_env[@]}" LATENTCRATE_REQUIRE_SAGE=1 \
      /usr/local/bin/latentcrate-gpu-smoke 2>&1 | tee "$report" \
      || smoke_status=$?
  else
    compose "$engine" exec comfy \
      "${smoke_env[@]}" /usr/local/bin/latentcrate-gpu-smoke 2>&1 | tee "$report" \
      || smoke_status=$?
  fi
  printf 'Saved GPU report to %s\n' "$report"
  if ((smoke_status != 0)); then
    printf 'LatentCrate: the GPU smoke test failed with status %d\n' "$smoke_status" >&2
    return "$smoke_status"
  fi
}
