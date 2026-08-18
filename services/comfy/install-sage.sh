#!/usr/bin/env bash
set -Eeuo pipefail

# Installs the SageAttention wheel produced by the sage-builder stage and
# extends the runtime Python environment manifest. This script is shared by
# the runtime-sage and runtime-frontend-git-sage image stages so both apply
# the identical install and manifest-rewrite sequence.

python -m pip install --no-cache-dir --no-deps /tmp/sage-wheels/*.whl
python -c "from sageattention.core import attn_false, get_cuda_arch_versions, per_block_int8_triton, per_channel_fp8, per_thread_int8_triton, per_warp_int8_cuda"
{
  printf '\n# runtime-sage\n'
  sha256sum \
    /usr/local/share/latentcrate/sageattention.commit \
    /usr/local/share/latentcrate/sageattention.cuda-arch-list \
    /usr/local/share/latentcrate/sageattention.wheel.sha256
  python -m pip freeze --all | sort
} >> /usr/local/share/latentcrate/python-environment.manifest
sha256sum /usr/local/share/latentcrate/python-environment.manifest \
  | cut -d ' ' -f 1 > /usr/local/share/latentcrate/python-environment.id
