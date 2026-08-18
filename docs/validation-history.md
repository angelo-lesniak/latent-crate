# Validation history

This page keeps dated successful validation results. It records the final
evidence from each validation period, not each intermediate debugging attempt.

## Arch Linux and RTX 5090 — 2026-08-17

- `doctor current` and `doctor edge` passed without warnings on native Arch
  Linux with rootless Podman 6.1.0, podman-compose 1.6.0, `crun`, NVIDIA driver
  610.57.04, and an RTX 5090 at compute capability 12.0. CDI, host identity,
  storage, and both CUDA architecture lists were correct.
- `init --node-set latent-nodepack edge` built the helper image and installed
  all ten public repositories transactionally at their declared commits.
  Standalone status reported every entry ready, and sync made no changes to the
  clean set.
- Third-party requirements were captured from the installed nodes. Reviewed
  exact-commit rules handled SAM2 and the WAS Node Suite Git dependencies
  without editing node source. The matching CUDA development stage built the
  dependency set, the final offline install passed, and `pip check` reported no
  broken requirements.
- Both `edge-sage` and `current-sage` built, started, and became healthy. The
  saved-dependency path reused completed build layers. The runtime used UID/GID
  1000 with the supplementary model group, kept caches below `/cache`, and
  contained neither NVCC nor the node wheelhouse.
- GPU checks passed for PyTorch 2.11 with CUDA 13.0, Triton 3.6, RTX 5090
  capability `sm120`, a real SageAttention kernel, the required FFmpeg
  capability inventory, frontend provenance, edge TorchCodec decoding, and the
  current profile's expected TorchCodec opt-out.
- The complete `smoke-gpu current` run passed with exact image/frontend
  provenance, CUDA, Triton, the real `sm120` Sage kernel, FFmpeg capabilities,
  and a 1920x1080 H.264 NVENC hardware encode.
- The Sage opt-out image built, started, and passed its matching smoke test.
- Recreating the edge container preserved the tracked input, output, workflow,
  and user-file inventory.

The unconditional container-recreation change passed the static and CLI
regression suites and the following native lifecycle run.

## Windows no-GPU validation — 2026-08-08 to 2026-08-16

These checks used an x86-64 Windows 11 host and a rootful Podman WSL machine.
That engine is outside the supported runtime, but it provided useful build and
CPU evidence.

- The static, policy, CLI, helper, Compose-rendering, ShellCheck, and hadolint
  gates passed with the test set available at the end of the period.
- Pinned helper images built and ran. Fixture node trees covered recursive
  requirement capture, offline hashed installation, empty and base-satisfied
  dependency sets, exact node sets, and interrupted transaction recovery.
- Current, current with SageAttention, and edge Docker-format images built.
  Their final runtimes excluded NVCC, CUDA development files, and the node
  wheelhouse. Edge retained its pinned TorchCodec and required NPP runtime
  libraries; CPU H.264 decoding and `pip check` passed.
- SageAttention compiled for compute capability 12.0 and imported without a
  GPU. PyTorch, Triton, FFmpeg software codecs, and NVIDIA encoder availability
  were inspected without claiming hardware execution.
- A hardened UID/GID 1000 CPU container started ComfyUI and Manager, served HTTP
  on `127.0.0.1:4207`, and preserved its database, workflows, settings, inputs,
  and outputs across recreation.
- Frontend release archives passed pinned digest and archive-safety checks.
  Public Git and local source builds completed with Node 26 and pinned pnpm;
  the local build used a network-disabled final install, typecheck, build, and
  atomic publish. Release and prebuilt-dist frontends were served by the CPU
  application.
