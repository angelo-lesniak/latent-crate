#!/usr/bin/env bash
set -Eeuo pipefail

printf '== LatentCrate GPU verification ==\n'
date --iso-8601=seconds

printf '\n== Build information ==\n'
while IFS= read -r -d '' file; do
  printf '%s=%s\n' "$(basename "$file")" "$(cat "$file")"
done < <(find /usr/local/share/latentcrate -maxdepth 1 -type f -print0 | sort -z)

# The image records the mode it built (git); the dist overlay replaces it.
frontend_mode=${LATENTCRATE_FRONTEND_IMAGE_MODE:-}
[[ "${COMFY_FRONTEND_MODE:-}" != dist ]] || frontend_mode=dist
printf 'running.frontend.mode=%s\n' "$frontend_mode"

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
