# LatentCrate

**Pinned ComfyUI containers for Docker and Podman.**

LatentCrate runs ComfyUI in a container with pinned versions of the components
that matter for reproducible workflows: ComfyUI, its frontend, the PyTorch and
CUDA stack, FFmpeg, Triton, SageAttention, and TorchCodec. The base images and
distribution packages underneath can still change between rebuilds, so the
full image is not bit-for-bit reproducible. Your models, workflows, and
outputs stay on your own computer. LatentCrate is built for consumer NVIDIA
graphics cards, and it makes deliberate choices for you instead of offering
every option.

Use it when you want to:

- keep a stable ComfyUI setup and a newer test setup side by side;
- use SageAttention and the KJNodes MiniMax H3 memory-efficient path;
- test a frontend fork or local frontend changes against a pinned backend;
- develop and test local nodes against pinned ComfyUI versions;
- rebuild third-party node dependencies instead of installing them on every start;
- use the same project with Docker Compose or rootless Podman.

> **Project status:** community preview. Static checks, no-GPU image builds,
> and CPU runtime diagnostics pass on Windows with a rootful Podman WSL machine.
> Real NVIDIA, native Docker, rootless Podman, and supported WSL2 checks are
> still in progress. See the [current validation status](docs/validation-status.md).

LatentCrate is an unofficial community project. It is not affiliated with Comfy
Org, NVIDIA, Docker, or Podman.

## Is LatentCrate a good fit?

LatentCrate makes deliberate choices for you instead of offering every option.
It is for people who are comfortable with large container builds and want
control over versions, native extensions, frontends, and host storage. If you
only need a simple ComfyUI installation, a desktop package or standard
installer will be easier.

The current scope is:

- x86-64 Linux containers with NVIDIA GPUs;
- Arch Linux as the main native-Linux target;
- Docker Engine with Compose v2, or rootless Podman with `crun` and Compose;
- NVIDIA Container Toolkit and CDI devices;
- compute capability 12.0 in the checked-in profiles.

The shipped version profiles (`current` and `edge`) are tuned for NVIDIA RTX
50-series cards, which have compute capability 12.0. RTX 50-series is the only
generation covered by the validation plan so far. Recent NVIDIA generations,
for example RTX 30- and 40-series, are expected to work after a one-time
profile edit: copy a profile, set both CUDA architecture lists to your card's
capability, and build with the new profile. No other generation has been
validated, and SageAttention must support the card's GPU architecture. Step
4 of the quickstart below shows the exact commands. AMD, ARM64, Jetson, native
Windows containers, Kubernetes, and public multi-user hosting are outside the
current scope.

Windows 10 and 11 can provide the Linux environment through WSL2. This path is
expected to work, but it is not yet GPU-validated. Run the LatentCrate commands
inside WSL2, not PowerShell, Command Prompt, or Git Bash.

### Support matrix

| Platform and engine | Status |
| --- | --- |
| Linux with Docker Engine and Compose v2 | Supported target; GPU validation in progress ([status](docs/validation-status.md)) |
| Linux with rootless Podman and `crun` | Supported target; GPU validation in progress ([status](docs/validation-status.md)) |
| Windows through WSL2 (Docker Desktop or Podman) | Expected to work; pending the [WSL2/NVIDIA checklist](docs/wsl2-nvidia-validation.md) |
| Native Windows containers, macOS, AMD, ARM64, Jetson | Not supported |

| NVIDIA GPU generation | Compute capability | Shipped profiles |
| --- | --- | --- |
| RTX 50-series (for example RTX 5080) | `12.0` | Work as shipped |
| RTX 40-series (for example RTX 4090) | `8.9` | Expected to work after a one-time profile edit; not validated |
| RTX 30-series (for example RTX 3090) | `8.6` | Expected to work after a one-time profile edit; not validated |

The capability values above are examples for common consumer cards. Confirm
your own card's value:

```bash
nvidia-smi --query-gpu=compute_cap --format=csv,noheader
```

## Start here

### 1. Prepare the host

You need:

- Bash, Git, `flock`, and `sha256sum`;
- an NVIDIA driver compatible with the selected CUDA version;
- [Docker Engine](https://docs.docker.com/engine/install/) with
  [Compose v2](https://docs.docker.com/compose/install/), or
  [rootless Podman](https://github.com/containers/podman/blob/main/docs/tutorials/rootless_tutorial.md)
  with `crun` and Compose;
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  with CDI devices generated.

Python, Node.js, npm, and pnpm are not needed on the host. LatentCrate runs its
helper tools in pinned containers.

Generate the CDI device list after installing the NVIDIA Container Toolkit.
The canonical example is:

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
```

> **Note:** the exact command and output path can change between toolkit
> versions. NVIDIA's [Container Toolkit documentation](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
> is authoritative.

Check that your GPU device is visible:

```bash
nvidia-ctk cdi list
```

The result should contain `nvidia.com/gpu=all`, unless you plan to select a
specific GPU.

Using WSL2? Read the short [WSL2 setup notes](docs/getting-started.md#windows-through-wsl2)
before continuing.

### 2. Get LatentCrate

Clone the repository, then enter it:

```bash
git clone https://github.com/angelo-lesniak/latent-crate.git latentcrate
cd latentcrate
```

Keep a WSL2 checkout under the Linux filesystem, for example
`~/src/latentcrate`, rather than `/mnt/c`.

### 3. Create your local configuration

```bash
bash bin/latentcrate init
```

This creates `.env` and the default host directories. Open `.env` and review at
least these values:

- `COMFY_DATA_DIR`: workflows, user settings, inputs, outputs, and managed nodes;
- `COMFY_MODELS_DIR`: your model library;
- `COMFY_CACHE_DIR`: downloaded and compiled caches;
- `GPU_DEVICE`: normally `nvidia.com/gpu=all`.

Relative paths are resolved from the repository root. Existing storage can be
used; models and ComfyUI data do not need to share a parent directory.

### 4. Check the host before a large build

```bash
bash bin/latentcrate doctor current
```

Fix every `[fail]` line. Read the `[warn]` lines before continuing. In
particular, `CUSTOM_NODE_CUDA_ARCH_LIST` and `SAGE_CUDA_ARCH_LIST` must cover
your GPU compute capability. Show your card's capability with:

```bash
nvidia-smi --query-gpu=compute_cap --format=csv,noheader
```

The shipped profiles pin capability `12.0` (RTX 50-series). If your card
reports another value, copy a profile once and edit it:

```bash
cp versions/current.env versions/my-gpu.env
# Edit CUSTOM_NODE_CUDA_ARCH_LIST and SAGE_CUDA_ARCH_LIST to your capability.
# Edit LATENTCRATE_TAG to my-gpu-sage so it matches the new profile name.
bash bin/latentcrate doctor my-gpu
bash bin/latentcrate up my-gpu --detach
```

`LATENTCRATE_TAG` names the image that the profile builds; it must be the
profile file name plus `-sage`. Use `my-gpu` instead of `current` in the later
commands.

### 5. Build and start ComfyUI

```bash
bash bin/latentcrate up current --detach
bash bin/latentcrate wait current
```

Open <http://127.0.0.1:4207> when `wait` reports that ComfyUI is healthy.
Generated files appear below `COMFY_DATA_DIR/output` on the host.

The SageAttention-capable image is the default. This makes workflow-level Sage
features available, including the KJNodes MiniMax H3 patch. It does **not** force
every workflow to use Sage. Set `LATENTCRATE_SAGE=false` in `.env`, or add
`--no-sage` to `up`, `build`, `config`, and `smoke-gpu`, if you need the smaller
base image.

The first build downloads large CUDA images and compiles FFmpeg and
SageAttention. It can take from tens of minutes to several hours, depending on
the network and CPU. Plan for at least 75 GB of free build space. Later builds
reuse caches when the relevant inputs have not changed.

If the first run fails, start with the [troubleshooting guide](docs/troubleshooting.md).

## Choose what to do next

| Goal | Start here |
| --- | --- |
| Use the newer pinned ComfyUI and frontend | `bash bin/latentcrate up edge --detach` |
| Look up any command or flag | [CLI reference](docs/cli.md) |
| Set up MiniMax H3 with SageAttention | [SageAttention guide](docs/sageattention.md) |
| Add nodes with ComfyUI Manager or local-only nodes | [Third-party node guide](docs/third-party-nodes.md) |
| Develop or test a local node | [Local node development](#local-node-development) |
| Test a public frontend fork or pull request | [Frontend modes](docs/frontends.md) |
| Build an uncommitted local frontend | [Local source mode](docs/frontends.md#local-source-mode) |
| Reuse an existing model library | [Storage layout](docs/storage.md) |
| Understand privacy and network access | [Privacy and containment](docs/privacy.md) |
| Update pinned versions | [Upgrade workflow](docs/upgrades.md) |
| Remove LatentCrate and free disk space | [Removal guide](docs/storage.md#removing-latentcrate-and-reclaiming-disk-space) |

## Version profiles

A version profile is a checked-in set of ComfyUI, frontend, CUDA, PyTorch,
FFmpeg, SageAttention, and build-tool versions.

| Profile | Intended use | TorchCodec |
| --- | --- | --- |
| `current` | Slower-changing default | Disabled |
| `edge` | Newer pinned components for deliberate testing | Enabled with matching CUDA NPP runtime libraries |

Both are exact selections. Neither silently downloads `latest` or updates
itself when the container starts. The backend and frontend are pinned
independently, so a newer frontend can be tested with a stable backend.

```bash
bash bin/latentcrate versions
```

Upstream references live in `versions/current.env` and `versions/edge.env`.
Resolved Git commits and build information are also stored inside each image.

## Third-party nodes and development

ComfyUI calls extensions "custom nodes." This guide usually calls them
third-party nodes, except when it refers to an exact ComfyUI name or path.
ComfyUI Manager installs third-party nodes from inside the interface; this
project calls it "Manager." Manager installs node
source under the host-mounted `COMFY_DATA_DIR/custom_nodes` directory, where it
remains between container restarts. LatentCrate keeps Manager's immediate
Python installs disposable. After adding or updating nodes, capture their
Python requirements and rebuild the image's dependency layer:

```bash
bash bin/latentcrate up current --detach
```

By default, every `up` and `build` re-reads your nodes' requirements and
rebuilds the dependency layer. Add `--use-saved-node-deps` to skip that step
for one command: your node files stay mounted, and the image is built from the
last saved requirements.

### Local node development

Put your node below `COMFY_LOCAL_NODES_DIR`, which defaults to
`local/custom_nodes`:

```text
local/custom_nodes/
  MyNode/
    __init__.py
    requirements.txt
```

Edit the source with your normal host tools, then build and start ComfyUI:

```bash
bash bin/latentcrate up current --detach
bash bin/latentcrate logs current
```

Run `up` again after a source change so ComfyUI loads the new code. Changed
Python requirements rebuild the dependency layer; unchanged dependencies reuse
the build cache. You can use the `current` or `edge` profile and combine this
with any supported frontend mode.

The local-node directory is read-only inside the container, but remains editable
on the host. It is not added to Git or the image build context. This workflow
does not provide hot reload. A node that needs extra operating-system libraries
may still need a reviewed Dockerfile change.

LatentCrate also supports commit-pinned node sets:

```bash
bash bin/latentcrate nodes list
bash bin/latentcrate init --node-set latent-nodepack current
bash bin/latentcrate up current --detach
```

Read the [third-party node guide](docs/third-party-nodes.md) before adding Git
requirements or native CUDA packages.

## Frontend development

The normal image uses the pinned release frontend. Trusted public Git source,
local source, and an existing built `dist/` directory are also supported:

```bash
# Public fork, branch, commit, or GitHub pull-request reference
bash bin/latentcrate up edge \
  --frontend-git https://github.com/your-name/ComfyUI_frontend.git your-branch \
  --detach

# Recommended for an uncommitted local checkout
bash bin/latentcrate up edge \
  --frontend-source /path/to/ComfyUI_frontend \
  --detach

# Frontend files built by another trusted system
bash bin/latentcrate up edge \
  --frontend-dist /path/to/ComfyUI_frontend/dist \
  --detach
```

Local source builds use a containerized Node/pnpm toolchain. The checkout is
mounted read-only, and only the completed `dist/` is served to ComfyUI. See
[frontend modes](docs/frontends.md) for the trust model and repeatable profile
setup.

## Storage at a glance

```text
Host path                         Container path       Purpose
COMFY_DATA_DIR                   /data                user state, inputs, outputs, managed nodes
COMFY_MODELS_DIR                 /models              shared model library
COMFY_CACHE_DIR                  /cache               downloads and compiled caches
COMFY_LOCAL_NODES_DIR            /local/custom_nodes  read-only local or private nodes
```

ComfyUI uses these paths directly. Container recreation does not remove your
workflows, outputs, models, or Manager state. See [storage layout](docs/storage.md).

## Privacy and remote access

The UI binds to `127.0.0.1` by default. Manager sharing and direct unreviewed
install paths are disabled. External ComfyUI API nodes remain available because
the upstream flag that removes them also blocks Manager's Extensions UI. Set
`COMFY_DISABLE_API_NODES=true` for a strict browser-offline mode, preferably
together with `COMFY_ENABLE_MANAGER=false`.

> **Warning:** these settings reduce accidental access. They do not make an
> unreviewed third-party node safe. Third-party nodes run inside ComfyUI and
> can access mounted data and the normal runtime network.

For remote use, prefer an SSH tunnel:

```bash
ssh -L 4207:127.0.0.1:4207 your-host
```

> **Warning:** the wrapper requires `COMFY_ALLOW_REMOTE=true` before it accepts
> a non-loopback bind address. Add authentication and TLS before exposing
> ComfyUI beyond a trusted network. See
> [privacy and containment](docs/privacy.md).

## Verify a GPU setup

After the container is healthy:

```bash
bash bin/latentcrate smoke-gpu current
```

The command checks CUDA, Torch, Triton, SageAttention, FFmpeg codecs, a real
Sage kernel, and a short NVENC hardware encode. It saves a report below
`reports/`.

Contributor checks that do not need a GPU are available with:

```bash
bash tests/static.sh
```

Do not describe a version profile as GPU-validated until the matching
[Arch/NVIDIA](docs/arch-nvidia-validation.md) or
[WSL2/NVIDIA](docs/wsl2-nvidia-validation.md) validation guide has passed.

## Documentation

The [documentation map](docs/index.md) groups the guides by task. The
[CLI reference](docs/cli.md) documents every wrapper command and flag. The
[glossary](docs/glossary.md) explains container and GPU terms used in this
project.

Use `bash bin/latentcrate ...` as the supported interface. Direct Compose calls
skip wrapper validation, generated cache inputs, and engine-specific overlays.

## Planned trainers

Trainer setups will live beside ComfyUI under `services/`. Kohya,
ai-toolkit, and Musubi are not included until each integration is complete and
testable. See [trainer integrations](docs/trainers.md).

## Contributing

Contributions are welcome within LatentCrate's focused support scope. See
[CONTRIBUTING.md](CONTRIBUTING.md) for development tools, tests, and validation
requirements. To report a security problem, follow [SECURITY.md](SECURITY.md).

## License

LatentCrate is available under the [MIT License](LICENSE). Container images also
include upstream software under its own licenses. Review
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before redistributing images.
