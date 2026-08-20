# Changelog

LatentCrate follows [Semantic Versioning](https://semver.org/) once the first
tagged release is published. Until then, notable changes are collected under
`Unreleased`.

## Unreleased

### Added

- Docker Compose and rootless Podman Compose support over one portable core.
- Pinned `current` and forward-looking `edge` version profiles.
- Release, trusted Git, containerized local-source, and prebuilt-dist frontend
  modes.
- SageAttention-capable images by default, with one
  `LATENTCRATE_SAGE=off|available|global` mode selecting the smaller opt-out
  image, workflow-level Sage, or global Sage, plus GPU smoke verification.
- Host-mounted storage, saved custom-node dependencies, and source/build
  records.
- Network-disabled, containerized custom-node snapshotting without a host Python
  requirement.
- An isolated runtime Python environment compatible with PEP 668, with
  reviewed PyTorch/Manager dependency constraints.
- A consolidated validation-status record of the current evidence.
- CUDA development-stage wheel builds and offline runtime installation for
  captured custom-node dependencies, with build details recorded.
- Default Manager settings that protect the core Torch/Triton stack while
  preserving existing user configuration.
- Shared-library FFmpeg builds with x265, expanded media verification, and
  edge-only pinned CUDA TorchCodec support.
- Exact tagged frontend release assets without anonymous GitHub API pagination.
- Opt-in API-node/browser-offline mode, Manager-sharing and metadata controls,
  remote-bind guards, and runtime containment.
- Contributor-facing CI improvements: configuration checks, reusable BuildKit
  caches, and configurable CPU runners.
- A thin CLI dispatcher with fixed core, frontend, dependency, and runtime Bash
  modules for easier extension and review.
- Optional commit-pinned custom-node sets, a read-only local-node source, and a
  default dependency refresh on `up` or `build` with an explicit opt-out.
- Reviewed VCS requirement overrides, including a cached SAM2 commit.
- An exact offline package lock for the final custom-node dependency install.
- A reviewed single-provider OpenCV policy and the image/X11 runtime libraries
  used by the included frame-interpolation, image, and video nodes.
- A task-focused documentation map, first-run guide, troubleshooting guide, and
  glossary for advanced users who are new to the project.
- A shorter NVIDIA-host validation guide with one baseline, focused checks for
  changed features, and separate maintainer release checks.
- Offline commands to list local-compatible official templates from a selected
  image and create reviewable model-set manifest drafts from embedded hints.
- Checksum-verified downloads of pinned model sets for selected official
  workflows (`models list`, `models fetch`, `models status`, and repeatable
  `up --model-set`).
