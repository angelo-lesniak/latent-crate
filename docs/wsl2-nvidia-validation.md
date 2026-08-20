# Windows, WSL2, and NVIDIA validation checklist

Windows support means running LatentCrate's Linux containers and Bash CLI from
a WSL2 distribution. It does not mean native Windows containers or direct
PowerShell operation. This path remains unverified until the applicable engine
completes every section below on real NVIDIA hardware.

Use the current official setup documentation for
[Docker Desktop GPU support](https://docs.docker.com/desktop/features/gpu/),
[Docker Desktop with WSL2](https://docs.docker.com/desktop/features/wsl/), or
[Podman Desktop GPU support](https://podman-desktop.io/docs/podman/gpu).

## 1. Record the Windows and WSL environment

- [ ] Confirm Windows 10 or 11, a current NVIDIA Windows driver with WSL GPU
      support, and WSL 2.1.5 or newer.
- [ ] From PowerShell, record `wsl --version` and `wsl --list --verbose`; confirm
      the selected distribution reports version 2.
- [ ] Record the selected engine and Compose versions and whether Docker Desktop
      or Podman Desktop supplies the engine.
- [ ] Do not install a Linux NVIDIA display driver inside WSL. Confirm
      `nvidia-smi` can query the Windows-provided GPU from the WSL distribution.

## 2. Use the WSL Linux filesystem

- [ ] Clone LatentCrate below the distribution filesystem, for example
      `~/src/latentcrate`, rather than below `/mnt/c`.
- [ ] Place the initial data, models, and cache directories in the WSL
      filesystem and confirm the engine can bind-mount them.
- [ ] Run all `bash bin/latentcrate ...` commands from the WSL shell, not
      PowerShell, Command Prompt, or Git Bash.
- [ ] Confirm files created in `/data` retain usable ownership and permissions
      after the container is recreated.

## 3. Validate the container engine and GPU path

- [ ] Confirm the selected engine is reachable from the WSL distribution and
      its Compose provider works.
- [ ] Confirm the engine and Compose clients run natively in the selected WSL
      distribution. Do not forward a Windows `podman.exe` into WSL unless its
      Compose provider demonstrably preserves Linux paths; Windows Compose can
      misinterpret `/mnt/c/...` as `C:\mnt\c\...`.
- [ ] For Podman, confirm `podman info --format '{{.Host.Security.Rootless}}'`
      reports `true`. The shared Podman overlay uses `keep-id`; a rootful Podman
      machine presents shared bind mounts as `root:root` and is not currently a
      supported runtime path.
- [ ] Follow the engine vendor's current NVIDIA GPU setup and run its minimal
      CUDA container test successfully before testing LatentCrate.
- [ ] Run `nvidia-ctk cdi list` and confirm it contains the configured
      `GPU_DEVICE`, normally `nvidia.com/gpu=all`.
- [ ] Run `bash bin/latentcrate doctor current`; resolve every failure and review
      every warning.
- [ ] Run `bash bin/latentcrate config current` and confirm Compose retains the
      requested GPU device and the intended WSL filesystem paths.

### Docker Desktop decision point

Docker Desktop's public GPU examples use `--gpus all`, while LatentCrate's
portable Compose file uses a CDI device name. Record whether the current
`devices: [nvidia.com/gpu=all]` request works without modification.

- [ ] If it works, retain the resulting Compose version, Docker Desktop version,
      GPU details, and `smoke-gpu` report as validation results.
- [ ] If it fails while Docker's minimal `--gpus all` test succeeds, capture the
      error. Test a dedicated Docker Desktop Compose overlay using the Compose
      `gpus` field before changing the portable configuration.
- [ ] Do not commit such an overlay or list Docker Desktop as supported until
      build, startup, smoke, persistence, and frontend-mode checks all pass.

## 4. Build and run LatentCrate

- [ ] Run `bash tests/static.sh`.
- [ ] Initialize and review `.env` with `bash bin/latentcrate init`.
- [ ] Build with `bash bin/latentcrate build current`.
- [ ] Start with `bash bin/latentcrate up current --detach`, then run
      `bash bin/latentcrate wait current`.
- [ ] Open the localhost URL from Windows and complete a representative image,
      audio, and video workflow.
- [ ] Recreate the container and confirm workflows, inputs, outputs, custom
      nodes, models, and Manager state persist.
- [ ] Confirm local-only nodes mount read-only from the WSL filesystem, and run
      an automatic dependency refresh after a Manager node update.
- [ ] Install and inspect the `latent-nodepack` node set; confirm ownership remains
      usable from WSL and the exact pinned commit is present.
- [ ] Fetch and verify one model set in WSL storage, then use its linked official
      workflow. Confirm a gated token does not appear in container inspection.
- [ ] Run `bash bin/latentcrate smoke-gpu current` and retain the report.
- [ ] Confirm Torch CUDA initialization, the Sage kernel, and the real FFmpeg
      NVENC encode pass. The normal `current` build is Sage-capable.
- [ ] Confirm x264/x265 and every declared FFmpeg inventory check passes with the
      injected Windows/WSL NVIDIA libraries.

## 5. Other variants

- [ ] Build and verify `--no-sage`; confirm the smaller image contains no Sage
      package and the matching smoke command does not run Sage checks.
- [ ] Build `edge` and confirm its pinned CUDA TorchCodec wheel decodes the smoke
      video and a representative real input.
- [ ] Test the pinned release frontend, a trusted `--frontend-git` build, and a
      containerized local `--frontend-source` build.
- [ ] Confirm the source checkout is mounted read-only, the offline build
      publishes into the WSL filesystem, and rerunning `up` serves changes
      without rebuilding the CUDA image.
- [ ] Test `--frontend-dist` separately when prebuilt asset input is intended to
      be supported.
- [ ] Repeat the complete checklist separately for Docker Desktop and Podman
      Desktop before describing them as equivalent; passing one does not test
      the other.

## 6. Validation results

- [ ] Record Windows, WSL, engine, Compose, NVIDIA driver, GPU, and LatentCrate
      version information.
- [ ] Retain `doctor`, build, readiness, `smoke-gpu`, and representative
      workflow results.
- [ ] Document any required Compose overlay and why the portable CDI request was
      insufficient.
- [ ] Update the [README support matrix](../README.md#support-matrix) only
      after this checklist passes on a clean checkout.
