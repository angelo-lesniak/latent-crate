# Validation status

This page summarizes the strongest current evidence. It does not list each failed
attempt made while reaching that result. Reusable test steps live in the
[Arch/NVIDIA](arch-nvidia-validation.md) and
[WSL2/NVIDIA](wsl2-nvidia-validation.md) validation guides. Dated completed
results are kept in [validation history](validation-history.md).

Each result applies only to the versions and environment recorded for its
dated run. After any change to a pinned component, the Dockerfile, the Compose
files, or the wrapper, repeat the relevant validation guide before reusing a
result.

## Status and evidence vocabulary

These terms keep one meaning across the README support matrix and the
documentation:

- **Supported**: the project intends to maintain the stated feature, version,
  or environment. This is a maintenance promise, not proof that it works.
- **Expected**: technical evidence supports the claim, but no recorded
  validation run has confirmed it.
- **Validated**: a defined procedure passed, with a dated record in
  [validation history](validation-history.md).
- **Unverified**: no recorded evidence supports the claim yet.

## Validated

### Native Arch Linux with RTX 5090 — 2026-08-17

The 2026-08-17 run on native Arch Linux with rootless Podman 6.1.0 and an RTX
5090 passed `doctor`, the node-set install, the third-party dependency build,
both Sage-variant image builds, the GPU checks, `smoke-gpu current`, the Sage
opt-out smoke test, and the container-recreation state check. UI-level
contents and Manager database behavior still need a manual check. The
[2026-08-17 record](validation-history.md#arch-linux-and-rtx-5090--2026-08-17)
is the main source for this result.

### Windows/WSL model-set checks — 2026-08-19

The 2026-08-19 run in Ubuntu WSL passed all 68 Python tests then present, with
the static, ShellCheck, hadolint, and podman-compose gates. The model-set
helper passed its download, gated 18.3 GB fetch, interruption-resume, offline
verification, and token-hygiene checks through a rootless Podman connection,
but its client ran on Windows, so this run did not validate the supported
Linux wrapper path or a GPU runtime. The
[2026-08-19 record](validation-history.md#windowswsl-model-set-validation--2026-08-19)
is the main source for this result.

### Windows no-GPU build and CPU checks — 2026-08-08 to 2026-08-16

The 2026-08-08 to 2026-08-16 runs used a rootful Podman WSL machine outside
the supported runtime. They built the current, current-with-SageAttention, and
edge images, served ComfyUI and Manager from a hardened CPU container that
preserved user state across recreation, and passed the frontend build and
provenance checks available at the time. The
[2026-08-08 to 2026-08-16 record](validation-history.md#windows-no-gpu-validation--2026-08-08-to-2026-08-16)
is the main source for this result.

### Manager Extensions UI reachability — 2026-08-19

The 2026-08-19 check confirmed that, with the default
`COMFY_DISABLE_API_NODES=false`, Manager's Extensions UI reaches
`https://api.comfy.org` on the native Arch Linux host and the Windows rootful
Podman WSL setup. The
[2026-08-19 record](validation-history.md#manager-extensions-ui-reachability--2026-08-19)
is the main source for this result.

### MiniMax H3 workflow run — 2026-08-19

The 2026-08-19 check confirmed a full MiniMax H3 image-to-video workflow run
on the native Arch Linux host and the Windows setup. A VRAM observation for
the memory-efficient Sage path was not recorded. The
[2026-08-19 record](validation-history.md#minimax-h3-workflow-run--2026-08-19)
is the main source for this result.

## Still unverified

Before calling a release fully GPU-validated:

- run `models fetch` and `up --model-set` through the supported wrapper on
  native Linux with rootless Podman and with Docker, including the full
  MiniMax H3 download; complete the full Krea-2 download when its workflow is
  validated;
- complete a clean-cache current build through the supported wrapper;
- rebuild with the expanded OpenCV/image runtime libraries and canonical
  `opencv-contrib-python` requirement, then confirm every included third-party
  node imports and only one OpenCV distribution is installed;
- run representative image, audio, video, and third-party-node workflows, and
  record a VRAM observation for the memory-efficient Sage path;
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
