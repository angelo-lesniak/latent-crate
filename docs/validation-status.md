# Validation status

This page summarizes the strongest current evidence. It does not list each failed
attempt made while reaching that result. Reusable test steps live in the
[Arch/NVIDIA](arch-nvidia-validation.md) and
[WSL2/NVIDIA](wsl2-nvidia-validation.md) validation guides. Dated completed
results are kept in [validation history](validation-history.md).

## Validated

### Native Arch Linux with RTX 5090

- `doctor current` and `doctor edge` passed without warnings using rootless
  Podman 6.1.0, podman-compose 1.6.0, and `crun`. Driver 610.57.04, CDI device
  discovery, compute capability 12.0, host identity, storage, and both CUDA
  architecture lists were accepted.
- The node-set helper installed all ten `latent-nodepack` repositories at their
  declared commits. Standalone status reported every node ready, and a clean
  sync made no changes.
- The full third-party dependency snapshot and package build completed,
  including the reviewed SAM2 and WAS Git rewrites. Packages installed offline
  into the isolated runtime tree, and `pip check` passed.
- The `edge-sage` and `current-sage` images built, started, and became healthy.
  Reusing saved node dependencies also completed and reused the expected build
  layers.
- The final runtime used UID/GID 1000 with the supplementary model group. NVCC
  and the node wheelhouse were absent; GCC, CMake, and Ninja remained available
  for Triton and PyTorch JIT work. Runtime caches stayed below `/cache`.
- GPU checks confirmed PyTorch 2.11 with CUDA 13.0, Triton 3.6, the RTX 5090
  `sm120` SageAttention build, a real Sage kernel, required FFmpeg capabilities,
  release-frontend provenance, edge TorchCodec decoding, and the expected
  current-profile TorchCodec opt-out.
- The complete `smoke-gpu current` command passed. It verified exact image and
  frontend provenance, the real `sm120` Sage kernel, required FFmpeg features,
  and a 1920x1080 H.264 NVENC encode.
- The Sage opt-out image also built, started, and passed its matching smoke test.
- Recreating the edge container preserved the tracked input, output, workflow,
  and user-file inventory. UI-level contents and Manager database behavior still
  need a manual check.

### Windows no-GPU diagnostics

- The static suite passes with 52 Python tests; three symbolic-link tests skip on
  Windows and pass in Linux helper containers. Repository policy, Bash syntax,
  CLI, lifecycle, entrypoint, doctor, frontend, exporter, Compose rendering,
  ShellCheck, and hadolint checks have passed.
- Docker-format Podman builds completed for current, current with SageAttention,
  and edge. A hardened CPU container served ComfyUI and Manager on
  `127.0.0.1:4207` and preserved user state across recreation.
- Release, prebuilt-dist, public Git, and container-built local frontend paths
  passed their available build or CPU-serving checks. The local source build
  completed its network-disabled final phase.

The Windows runs used a rootful Podman WSL machine. They are useful diagnostics,
but they do not validate the supported rootless or GPU runtime.

## Still unverified

Before calling a release fully GPU-validated:

- complete a clean-cache current build through the supported wrapper;
- rebuild with the expanded OpenCV/image runtime libraries and canonical
  `opencv-contrib-python` requirement, then confirm every included third-party
  node imports and only one OpenCV distribution is installed;
- adopt the new `COMFY_DISABLE_API_NODES=false` default on the existing host and
  confirm Manager's Extensions UI can reach `https://api.comfy.org`;
- run representative image, audio, video, third-party-node, and MiniMax H3
  workflows, including a VRAM observation for the memory-efficient Sage path;
- confirm UI-visible persistence, Manager database behavior, automatic Manager
  dependency refresh, dirty node-set refusal, and a real local-only node;
- validate the remaining frontend modes on the NVIDIA host;
- validate native Docker parity and the separate WSL2/NVIDIA paths;
- run the public CI workflows, image scan, SBOM and license review, and verify
  Buildx cache reuse on clean runners.

Passing static or CPU checks does not complete these items. When native
validation is completed, add a dated record to
[validation history](validation-history.md) with the version profile, image
digest, engine versions, GPU, driver, and generated reports.
