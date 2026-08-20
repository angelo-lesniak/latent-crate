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

- The static suite passes in Ubuntu WSL with all 68 Python tests and no skips.
  Repository policy, Bash syntax, CLI, lifecycle, entrypoint, doctor, frontend,
  and exporter checks pass. The pinned ShellCheck and hadolint containers also
  pass, as does the hermetic podman-compose 1.6 compatibility suite.
- Model-set unit tests cover strict manifests, shared-file deduplication,
  conflicting destinations, resumable staging, access preflight, checksum
  cleanup, and atomic no-overwrite publication with a tiny local fixture. All
  24 shipped file entries match the filename, size, and SHA-256 metadata at
  their immutable Hugging Face revisions. All seven unique commit-pinned
  official workflow files and all six immutable license references resolve.
- The model-set image built and ran as UID/GID 1000 through rootless Podman
  5.5.2 with `crun`. It downloaded and offline-verified a small public file.
  Hugging Face dry-run checks confirmed token access to all 16 unique files in
  the six shipped sets.
- The gated `flux2-klein-9b-distilled` set downloaded all 18.3 GB through the
  real Xet path. Every file passed size and SHA-256 verification. A separate
  network-disabled, read-only status container verified the finished set.
- A controlled interruption preserved a 775 MB partial FLUX file. The next run
  resumed and completed it, followed by another offline checksum pass. A
  network-disabled fetch then reused the complete set without a token.
- Inspection of the live token-fed helper contained neither the token value nor
  the `HF_TOKEN` and internal token variable names.
- Docker-format Podman builds completed for current, current with SageAttention,
  and edge. A hardened CPU container served ComfyUI and Manager on
  `127.0.0.1:4207` and preserved user state across recreation.
- Release, prebuilt-dist, public Git, and container-built local frontend paths
  passed their available build or CPU-serving checks. The local source build
  completed its network-disabled final phase.

Earlier full-image Windows runs used a rootful Podman WSL machine. The model-set
checks above used its separate rootless connection. They validate the helper and
Linux container behavior, but not the supported wrapper path because the client
still ran on Windows rather than inside Linux.

## Still unverified

Before calling a release fully GPU-validated:

- run `models fetch` and `up --model-set` through the supported wrapper on
  native Linux with rootless Podman and with Docker; complete full Krea-2 and
  MiniMax H3 downloads when those workflows are validated;
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
