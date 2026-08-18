# Third-party software

LatentCrate's source code is MIT licensed. Built images are aggregates containing
ComfyUI, PyTorch, CUDA runtime components, FFmpeg, codec libraries, optional
TorchCodec, SageAttention, Python packages, and optional custom-node
dependencies under their respective licenses.

In particular, LatentCrate enables FFmpeg's GPL components for libx264 and
libx265 support. It intentionally does not use FFmpeg's `--enable-nonfree`
option. Anyone
redistributing an image is responsible for preserving required notices,
providing corresponding source where a license requires it, and reviewing the
licenses introduced by custom-node dependency snapshots.

Trusted Git frontend builds execute the selected checkout's Node dependency and
build scripts in an intermediate stage. Only compiled frontend assets enter the
runtime, but their GPL and bundled-dependency obligations still apply to anyone
redistributing the resulting image.

Local source builds execute the mounted checkout's dependency and build logic in
isolated helper containers and publish only its compiled assets. Container
isolation does not change the licenses or trust requirements of that source and
its dependencies.

Authoritative upstream projects include:

- <https://github.com/Comfy-Org/ComfyUI>
- <https://github.com/pytorch/pytorch>
- <https://github.com/FFmpeg/FFmpeg>
- <https://gitlab.com/AOMediaCodec/SVT-AV1>
- <https://github.com/thu-ml/SageAttention>
- <https://github.com/meta-pytorch/torchcodec>
- <https://github.com/Comfy-Org/ComfyUI-Manager>
