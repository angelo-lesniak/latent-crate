# Documentation

Start with the path that matches what you want to do. You do not need to read
every page before using LatentCrate.

## First setup

- [Getting started](getting-started.md): prepare Linux or WSL2, configure the
  host, and complete the first Sage-capable launch.
- [Troubleshooting](troubleshooting.md): common errors, checks, and fixes.
- [Glossary](glossary.md): short explanations of GPU and container terms.

## Everyday tasks

- [CLI reference](cli.md): every `bin/latentcrate` command and flag.
- [Configuration](configuration.md): `.env` settings and version profiles.
- [Storage layout](storage.md): models, user data, caches, and local nodes.
- [GPU and container-engine support](gpu-support.md): CDI, CUDA architectures,
  Docker, Podman, and WSL2 limits.
- [Third-party nodes](third-party-nodes.md): Manager updates, saved dependencies, pinned
  node sets, private nodes, and native packages.
- [SageAttention](sageattention.md): Sage defaults and the MiniMax H3 path.
- [Frontend modes](frontends.md): pinned releases, public Git source, local
  source, and prebuilt `dist/` assets.
- [Privacy and containment](privacy.md): safe defaults and their limits.

## Maintenance and validation

- [Image build flow](build-flow.md): how Dockerfile stages feed the final image
  variants.
- [Upgrade workflow](upgrades.md): change pinned components carefully, and
  update LatentCrate itself.
- [Removing LatentCrate](storage.md#removing-latentcrate-and-reclaiming-disk-space):
  uninstall and reclaim disk space.
- [Security policy](../SECURITY.md): how to report a security problem.
- [Validation status](validation-status.md): what has and has not been tested.
- [Validation history](validation-history.md): dated test records.
- [Arch/NVIDIA validation](arch-nvidia-validation.md): baseline and focused
  native GPU checks.
- [WSL2/NVIDIA checklist](wsl2-nvidia-validation.md): Windows and WSL2 checks.
- [Continuous integration](ci.md): image builds, caches, and GPU test limits.
- [Trainer integrations](trainers.md): plans for future trainer setups.

Contributors should also read [CONTRIBUTING.md](../CONTRIBUTING.md).
