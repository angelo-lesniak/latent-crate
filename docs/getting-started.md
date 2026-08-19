# Getting started

This guide takes you from a prepared NVIDIA host to a running, Sage-capable
ComfyUI container. The short version is in the [README](../README.md#start-here).

## Before you begin

LatentCrate is a good fit when you want pinned versions, SageAttention, custom
frontend builds, or reviewable third-party node dependencies. It is not the shortest
way to install a basic ComfyUI desktop setup.

You need an x86-64 Linux environment, an NVIDIA GPU, and enough storage for
large CUDA build layers. Plan for at least 75 GB of free build space. The first
build can take from tens of minutes to several hours. Later builds normally
reuse most unchanged layers.

Install these host tools:

- Bash and Git;
- `flock` from util-linux and `sha256sum` from GNU coreutils;
- [Docker Engine](https://docs.docker.com/engine/install/) with
  [Compose v2](https://docs.docker.com/compose/install/), or
  [rootless Podman](https://github.com/containers/podman/blob/main/docs/tutorials/rootless_tutorial.md)
  with `crun` and Compose;
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html);
- a driver compatible with the CUDA version in the selected profile.

Python and Node.js are contributor or build-container tools. Normal LatentCrate
use does not require them on the host.

## Linux setup

Generate or refresh the NVIDIA CDI configuration. The canonical example is:

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
```

> **Note:** the exact command and output path can change between toolkit
> versions. NVIDIA's [install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
> is authoritative.

Confirm the selected device exists:

```bash
nvidia-ctk cdi list
```

LatentCrate normally requests:

```text
nvidia.com/gpu=all
```

If both Docker and Podman are installed, LatentCrate selects a supported engine
automatically. Set `CONTAINER_ENGINE=docker` or `CONTAINER_ENGINE=podman` in
`.env` when you want a fixed choice. Podman must be rootless and use `crun`.

## Windows through WSL2

Use Docker Desktop with its WSL2 backend or a WSL2-backed Podman machine.
Podman must be rootless and use `crun` for the supported path.

Run every `bash bin/latentcrate ...` command inside the integrated WSL2
distribution. Do not run the wrapper from PowerShell, Command Prompt, or Git
Bash. Do not let WSL select a Windows `docker.exe` or `podman.exe` as its client.

Keep the repository and active data in the WSL Linux filesystem:

```text
~/src/latentcrate
~/latentcrate-data
```

Avoid `/mnt/c` for build caches and frequently changed data. Crossing the
Windows/Linux filesystem boundary makes bind mounts much slower.

Before relying on WSL2 for important work, complete the
[WSL2/NVIDIA validation checklist](wsl2-nvidia-validation.md). Docker Desktop's
CDI-based Compose path still needs real-host validation.

## First launch

Clone the repository, then enter it:

```bash
git clone https://github.com/angelo-lesniak/latent-crate.git latentcrate
cd latentcrate
```

Create local configuration and directories:

```bash
bash bin/latentcrate init
```

Open `.env`. Check the storage paths and `GPU_DEVICE`. The defaults keep
everything below the repository, which is useful for a first test. Point
`COMFY_MODELS_DIR` at an existing model library if needed.

Check the host before downloading the large image layers:

```bash
bash bin/latentcrate doctor current
```

A ready host has no `[fail]` lines. Warnings can describe an unvalidated
platform or an optional feature, so read them rather than ignoring them.

The checked-in profiles build native code for compute capability 12.0, which
matches NVIDIA RTX 50-series cards. Show your card's capability with:

```bash
nvidia-smi --query-gpu=compute_cap --format=csv,noheader
```

If your GPU reports another value, copy a profile once and edit it:

```bash
cp versions/current.env versions/my-gpu.env
# Edit CUSTOM_NODE_CUDA_ARCH_LIST and SAGE_CUDA_ARCH_LIST to your capability.
# Edit LATENTCRATE_TAG to my-gpu-sage so it matches the new profile name.
bash bin/latentcrate doctor my-gpu
bash bin/latentcrate up my-gpu --detach
```

`LATENTCRATE_TAG` names the image that the profile builds; it must be the
profile file name plus `-sage`. Use `my-gpu` instead of `current` in the later
commands. That combination is not described as supported until you complete
the GPU checks on the real host.

Build and start the default profile:

```bash
bash bin/latentcrate up current --detach
bash bin/latentcrate wait current
```

The first command may be quiet for long periods while native components
compile. It ends by starting the container. The second command exits when the
health check passes. Open <http://127.0.0.1:4207>.

The default image includes SageAttention. Workflow-level Sage features are
available, but ComfyUI does not replace attention globally. To build without
Sage, use:

```bash
bash bin/latentcrate up current --no-sage --detach
```

You can make that choice persistent with `LATENTCRATE_SAGE=false` in `.env`.
Use `doctor current --no-sage` when checking a one-off opt-out build.

## Confirm persistence

Create a small workflow or output, then recreate the container:

```bash
bash bin/latentcrate down current
bash bin/latentcrate up current --detach
bash bin/latentcrate wait current
```

The workflow and output should still be present because `/data` is mounted from
the host. Container images and user data have separate lifecycles.

## Useful first commands

```bash
bash bin/latentcrate status current       # Show the ComfyUI service
bash bin/latentcrate logs current         # Follow logs
bash bin/latentcrate versions             # Show pinned profile values
bash bin/latentcrate smoke-gpu current    # Run the real GPU and media checks
bash bin/latentcrate down current         # Stop and remove the container
```

For MiniMax H3, continue with the [SageAttention guide](sageattention.md). For
Manager or private nodes, continue with the
[third-party node guide](third-party-nodes.md). To fetch the pinned files for a
ready workflow, see [Model sets](model-sets.md).
