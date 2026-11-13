# Third-party software

LatentCrate's source code is MIT licensed. Built images are aggregates containing
ComfyUI, PyTorch, CUDA runtime components, FFmpeg, codec libraries,
TorchCodec, optional SageAttention, Python packages, and optional custom-node
dependencies under their respective licenses.

In particular, LatentCrate enables FFmpeg's GPL components for libx264 and
libx265 support. It intentionally does not use FFmpeg's `--enable-nonfree`
option. Anyone redistributing an image is responsible for preserving required
notices, providing corresponding source where a license requires it, and
reviewing the licenses of the custom-node requirements the image build
installs.

Release and Git modes run the checkout's build scripts in a build stage. Only
compiled frontend assets enter the runtime, but their GPL and bundled-dependency
obligations still apply to anyone redistributing the resulting image.

A source-mode frontend build runs the checkout's dependency install and build
scripts in the pinned Node image and publishes only its compiled assets.
Container isolation does not change the licenses or trust requirements of that
source and its dependencies.

Model-set manifests refer to model weights under their upstream licenses.
Those weights are downloaded to user-controlled storage and are not distributed
under LatentCrate's MIT license. Users must review and accept each model license.

Authoritative upstream projects include:

- <https://github.com/Comfy-Org/ComfyUI>
- <https://github.com/Comfy-Org/ComfyUI_frontend> (GPL-3.0, built into the `runtime` and `runtime-sage` images)
- <https://github.com/nodejs/node> (runs the frontend build)
- <https://github.com/pytorch/pytorch>
- <https://github.com/FFmpeg/FFmpeg>
- <https://github.com/FFmpeg/nv-codec-headers>
- <https://gitlab.com/AOMediaCodec/SVT-AV1>
- <https://github.com/thu-ml/SageAttention>
- <https://github.com/meta-pytorch/torchcodec>
- <https://github.com/Comfy-Org/ComfyUI-Manager>
