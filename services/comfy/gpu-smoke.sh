#!/usr/bin/env bash
set -Eeuo pipefail

expect_value() {
  local description=$1
  local expected=$2
  local actual=$3

  [[ -n "$expected" ]] || return
  if [[ "$actual" != "$expected" ]]; then
    printf 'LatentCrate: %s mismatch (expected %s, running %s).\n' \
      "$description" "$expected" "${actual:-missing}" >&2
    exit 1
  fi
}

printf '== LatentCrate GPU verification ==\n'
date --iso-8601=seconds

printf '\n== Build information ==\n'
while IFS= read -r -d '' file; do
  printf '%s=%s\n' "$(basename "$file")" "$(cat "$file")"
done < <(find /usr/local/share/latentcrate -maxdepth 1 -type f -print0 | sort -z)

frontend_root=${COMFY_FRONTEND_ROOT:-/opt/latentcrate-frontend}
frontend_content_id=$(
  export LC_ALL=C
  cd "$frontend_root"
  find . -type f -print0 \
    | sort -z \
    | xargs -0 sha256sum \
    | sha256sum \
    | cut -d ' ' -f 1
)
# The image records the mode it built (git); the dist overlay replaces it.
frontend_mode=${LATENTCRATE_FRONTEND_IMAGE_MODE:-}
[[ "${COMFY_FRONTEND_MODE:-}" != dist ]] || frontend_mode=dist
printf 'running.frontend.mode=%s\n' "$frontend_mode"
printf 'running.frontend.content.sha256=%s\n' "$frontend_content_id"
expect_value \
  'ComfyUI source reference' \
  "${LATENTCRATE_EXPECT_COMFYUI_REF:-}" \
  "$(cat /usr/local/share/latentcrate/comfyui.ref 2>/dev/null || true)"
expect_value \
  'PyTorch devel image' \
  "${LATENTCRATE_EXPECT_PYTORCH_DEVEL_IMAGE:-}" \
  "$(cat /usr/local/share/latentcrate/pytorch-devel.ref 2>/dev/null || true)"
expect_value \
  'PyTorch runtime image' \
  "${LATENTCRATE_EXPECT_PYTORCH_RUNTIME_IMAGE:-}" \
  "$(cat /usr/local/share/latentcrate/pytorch-runtime.ref 2>/dev/null || true)"
expect_value \
  'FFmpeg source reference' \
  "${LATENTCRATE_EXPECT_FFMPEG_REF:-}" \
  "$(cat /usr/local/share/latentcrate/ffmpeg.ref 2>/dev/null || true)"
expect_value \
  'NV codec headers source reference' \
  "${LATENTCRATE_EXPECT_NV_CODEC_HEADERS_REF:-}" \
  "$(cat /usr/local/share/latentcrate/nv-codec-headers.ref 2>/dev/null || true)"
expect_value \
  'SVT-AV1 source reference' \
  "${LATENTCRATE_EXPECT_SVT_AV1_REF:-}" \
  "$(cat /usr/local/share/latentcrate/svt-av1.ref 2>/dev/null || true)"
expect_value \
  'custom-node CUDA architecture list' \
  "${LATENTCRATE_EXPECT_CUSTOM_NODE_CUDA_ARCH_LIST:-}" \
  "$(cat /usr/local/share/latentcrate/custom-node.cuda-arch-list 2>/dev/null || true)"
expect_value \
  'frontend mode' \
  "${LATENTCRATE_EXPECT_FRONTEND_MODE:-}" \
  "$frontend_mode"
expect_value \
  'Sage image state' \
  "${LATENTCRATE_EXPECT_SAGE:-}" \
  "${LATENTCRATE_SAGE_ENABLED:-0}"
case "$frontend_mode" in
  git)
    expect_value \
      'frontend Git URL' \
      "${LATENTCRATE_EXPECT_FRONTEND_GIT_URL:-}" \
      "$(cat /usr/local/share/latentcrate/frontend.git-url 2>/dev/null || true)"
    expect_value \
      'frontend Git commit' \
      "${LATENTCRATE_EXPECT_FRONTEND_COMMIT:-}" \
      "$(cat /usr/local/share/latentcrate/frontend.commit 2>/dev/null || true)"
    expect_value \
      'frontend Node image' \
      "${LATENTCRATE_EXPECT_FRONTEND_NODE_IMAGE:-}" \
      "$(cat /usr/local/share/latentcrate/frontend.node-image 2>/dev/null || true)"
    expect_value \
      'frontend pnpm version' \
      "${LATENTCRATE_EXPECT_FRONTEND_PNPM_VERSION:-}" \
      "$(cat /usr/local/share/latentcrate/frontend.pnpm-version 2>/dev/null || true)"
    ;;
  dist)
    expect_value \
      'dist frontend content' \
      "${LATENTCRATE_EXPECT_FRONTEND_CONTENT_SHA256:-}" \
      "$frontend_content_id"
    ;;
esac
if [[ "${LATENTCRATE_SAGE_ENABLED:-0}" == 1 ]]; then
  expect_value \
    'SageAttention source reference' \
    "${LATENTCRATE_EXPECT_SAGEATTENTION_REF:-}" \
    "$(cat /usr/local/share/latentcrate/sageattention.ref 2>/dev/null || true)"
  expect_value \
    'SageAttention CUDA architecture list' \
    "${LATENTCRATE_EXPECT_SAGE_CUDA_ARCH_LIST:-}" \
    "$(cat /usr/local/share/latentcrate/sageattention.cuda-arch-list 2>/dev/null || true)"
fi

torchcodec_version=$(python -c 'from importlib.metadata import version; print(version("torchcodec"))')
expect_value \
  'TorchCodec version' \
  "${LATENTCRATE_EXPECT_TORCHCODEC_VERSION:-}" \
  "$torchcodec_version"
if [[ "$frontend_mode" != dist && -r /usr/local/share/latentcrate/frontend.content.sha256 ]]; then
  recorded_frontend_content_id=$(< /usr/local/share/latentcrate/frontend.content.sha256)
  if [[ "$frontend_content_id" != "$recorded_frontend_content_id" ]]; then
    printf 'LatentCrate: served frontend content does not match the image build record.\n' >&2
    exit 1
  fi
fi

printf '\n== NVIDIA ==\n'
nvidia-smi

printf '\n== Torch and Triton ==\n'
python - <<'PY'
import importlib.metadata
import torch
import triton

assert torch.cuda.is_available(), "torch.cuda.is_available() is false"
device = torch.cuda.current_device()
print(f"torch={torch.__version__}")
print(f"torch_cuda={torch.version.cuda}")
print(f"triton={importlib.metadata.version('triton')}")
print(f"device={torch.cuda.get_device_name(device)}")
print(f"capability={torch.cuda.get_device_capability(device)}")
PY

if [[ "${LATENTCRATE_REQUIRE_SAGE:-0}" == 1 && "${LATENTCRATE_SAGE_ENABLED:-0}" != 1 ]]; then
  printf '\nLatentCrate: SageAttention was required, but the running container is not Sage-capable.\n' >&2
  printf 'Recreate it with a Sage-capable image: bash bin/latentcrate up --sage available --detach\n' >&2
  exit 1
fi

if [[ "${LATENTCRATE_SAGE_ENABLED:-0}" == 1 ]]; then
  printf '\n== SageAttention ==\n'
  python - <<'PY'
import torch
from sageattention import sageattn
from sageattention.core import (
    attn_false,
    get_cuda_arch_versions,
    per_block_int8_triton,
    per_channel_fp8,
    per_thread_int8_triton,
    per_warp_int8_cuda,
)

print(f"architectures={get_cuda_arch_versions()}")
q = torch.randn(1, 4, 128, 64, device="cuda", dtype=torch.float16)
k = torch.randn_like(q)
v = torch.randn_like(q)
result = sageattn(q, k, v, tensor_layout="HND", is_causal=False)
torch.cuda.synchronize()
assert result.shape == q.shape
assert torch.isfinite(result).all()
print(f"sageattention_result={tuple(result.shape)}")
PY
else
  python - <<'PY'
import importlib.util

if importlib.util.find_spec("sageattention") is not None:
    raise SystemExit("SageAttention is installed in an image target that disables it")
print("sageattention_absent=true")
PY
  printf '\nSageAttention is not installed in this image target; skipping its kernel test.\n'
fi

printf '\n== FFmpeg capability inventory ==\n'
ffmpeg -version
ffmpeg -buildconf
ffmpeg -hide_banner -hwaccels
encoder_list=$(ffmpeg -hide_banner -encoders)
printf '%s\n' "$encoder_list"
ffmpeg -hide_banner -decoders
ffmpeg -hide_banner -filters

printf '\n== Required FFmpeg encoders ==\n'
for encoder in h264_nvenc hevc_nvenc av1_nvenc libsvtav1 libx264 libx265; do
  if ! grep -Eq "[[:space:]]${encoder}([[:space:]]|$)" <<< "$encoder_list"; then
    printf 'LatentCrate: required FFmpeg encoder is missing: %s\n' "$encoder" >&2
    exit 1
  fi
  grep -E "[[:space:]]${encoder}([[:space:]]|$)" <<< "$encoder_list"
done

printf '\n== TorchCodec decode ==\n'
torchcodec_smoke_file=$(mktemp --suffix=.mp4)
trap 'rm -f -- "$torchcodec_smoke_file"' EXIT
ffmpeg \
  -hide_banner \
  -loglevel error \
  -f lavfi \
  -i color=size=64x64:rate=1:color=black \
  -frames:v 2 \
  -c:v libx264 \
  -y "$torchcodec_smoke_file"
TORCHCODEC_SMOKE_FILE="$torchcodec_smoke_file" python - <<'PY'
import os

from torchcodec.decoders import VideoDecoder

decoder = VideoDecoder(os.environ["TORCHCODEC_SMOKE_FILE"], device="cpu")
frame = decoder[0]
assert tuple(frame.shape) == (3, 64, 64), frame.shape
print(f"torchcodec_frame={tuple(frame.shape)}")
PY
rm -f -- "$torchcodec_smoke_file"
trap - EXIT

printf '\n== NVENC hardware encode ==\n'
nvenc_smoke_size=1920x1080
printf 'nvenc_input=%s@24fps\n' "$nvenc_smoke_size"
ffmpeg \
  -hide_banner \
  -loglevel error \
  -f lavfi \
  -i "color=size=${nvenc_smoke_size}:rate=24:color=black" \
  -t 1 \
  -c:v h264_nvenc \
  -f null -

printf '\nLatentCrate GPU verification passed.\n'
