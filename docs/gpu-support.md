# GPU and container-engine support

The supported image and host architecture is x86-64. ARM64 and Jetson systems
need a different CUDA/PyTorch and native-library design.

LatentCrate uses NVIDIA Container Device Interface names in the portable Compose
file. Both supported engines consume the same device by default (override with
`GPU_DEVICE`):

```text
nvidia.com/gpu=all
```

List devices with:

```bash
nvidia-ctk cdi list
```

The selected images use CUDA 13.x. NVIDIA documents driver major 580 as the
minimum for CUDA 13.x minor-version compatibility; [`doctor`](cli.md#doctor)
enforces the value recorded in the selected version profile.

Avoid combining CDI with NVIDIA OCI hook-based device injection. CUDA base
images inherit `NVIDIA_VISIBLE_DEVICES=all`; LatentCrate explicitly changes it
to `void` so another runtime path does not inject a second device set. The
Compose CDI request is the sole source of GPU selection.

The defaults for `CUSTOM_NODE_CUDA_ARCH_LIST` and `SAGE_CUDA_ARCH_LIST` target
NVIDIA compute capability 12.0, which matches RTX 50-series cards. Show your
card's capability with:

```bash
nvidia-smi --query-gpu=compute_cap --format=csv,noheader
```

Set both architecture lists for another GPU when you use both native-package
variants. `doctor` compares the detected capability with the selected profile.
It rejects an incompatible custom-node build and an incompatible opted-in Sage
build. With Sage off, a Sage-only mismatch is a warning. Other architectures
must not be described as validated until `smoke-gpu` and a representative
workflow pass on real hardware.

FFmpeg is built with NVENC plus CPU AV1 support. A listed encoder proves only
that FFmpeg compiled against its headers; `bash bin/latentcrate smoke-gpu`
performs a real hardware H.264 encode.

The full supported smoke path therefore assumes an NVENC-capable NVIDIA GPU.
Compute-only cards can still run ComfyUI, but they do not satisfy LatentCrate's
media validation and the NVENC portion will fail explicitly.

Rootless Podman and conventional Linux Docker Engine on native Linux are the
intended engine targets. Windows can provide the required Linux execution
environment through WSL2, but native Windows containers are not supported.

On Windows, run LatentCrate from a WSL2 distribution connected to Docker
Desktop's WSL2 backend or a WSL2-backed Podman environment. The portable Compose
file continues to request `nvidia.com/gpu=all`. Podman documents CDI support for
its WSL2 machine. Docker Desktop documents GPU access with `--gpus all` instead.
Because LatentCrate uses CDI, Docker Desktop is not called validated until the
CDI request passes the
[WSL2/NVIDIA checklist](wsl2-nvidia-validation.md) on real hardware. If Docker
Desktop requires Compose's `gpus` field instead, add the `gpus` request as a
tested engine overlay rather than silently weakening the shared CDI
configuration.

The CLI and Compose provider must be native to, or correctly integrated with,
the selected WSL distribution. Calling the Windows Podman client from WSL is not
equivalent: its Windows Compose provider might reinterpret Linux paths. The
current Podman overlay also requires a rootless engine; Podman Desktop machines
configured as rootful need a separate, explicitly tested identity and
bind-mount design.

Docker receives the optional `HOST_MODEL_GID` as a supplementary group. Podman
uses `keep-groups`, which requires the `crun` OCI runtime. The host doctor checks
that prerequisite; actual shared-storage access remains part of the Arch
validation guide.
