# LatentCrate

**Pinned ComfyUI containers for Docker and Podman.**

LatentCrate runs ComfyUI in a container with pinned versions of the components
that matter for stable workflows: ComfyUI, its frontend, the PyTorch and CUDA
stack, FFmpeg, Triton, SageAttention, and TorchCodec. The versions you tested
are the versions you run: nothing updates itself when the container starts,
and your models, workflows, and outputs stay on your own computer. LatentCrate
is built for consumer NVIDIA graphics cards.

Use it when you want to:

- keep a stable ComfyUI setup and a newer test setup side by side;
- optionally use SageAttention with KJNodes' MiniMax H3 Sage patch nodes;
- test a frontend fork or local frontend changes against a pinned backend;
- develop and test local nodes against pinned ComfyUI versions;
- rebuild third-party node dependencies instead of installing them on every start;
- fetch verified model sets for selected official ComfyUI workflows;
- use the same project with Docker Compose or rootless Podman.

LatentCrate is an unofficial community project. It is not affiliated with Comfy
Org, NVIDIA, Docker, or Podman.

## Start here

The host needs Bash, Git, `flock`, and `sha256sum`; an NVIDIA driver for the
selected CUDA version; Docker Engine with Compose v2 or rootless Podman with
`crun`; and the NVIDIA Container Toolkit with CDI devices generated. The first
build can take from tens of minutes to several hours, the MiniMax H3 model set
downloads about 44 GB, and the build needs at least 75 GB of free disk space.
On a prepared host, run:

```bash
git clone https://github.com/angelo-lesniak/latent-crate.git latentcrate
cd latentcrate
bash bin/latentcrate init edge
bash bin/latentcrate doctor edge
bash bin/latentcrate up edge --model-set minimax-h3-i2v
```

`init` creates `.env`. The last command downloads the selected models, builds
the image, and keeps ComfyUI in the foreground so you can see its logs. In a
second terminal, `bash bin/latentcrate wait edge` exits when ComfyUI is healthy.
Then open <http://127.0.0.1:4207>, open the template browser, and choose
**MiniMax H3: Image to Video**. Press **Ctrl+C** in the first terminal to stop
ComfyUI. There is no background process to remember for this path.

If the host is not prepared, `doctor` reports a problem, or your GPU is not an
RTX 50-series card, follow the [getting-started guide](docs/getting-started.md).
If a command fails, start with the
[troubleshooting guide](docs/troubleshooting.md). When ComfyUI is running, pick
the next task from the table below.

## Choose what to do next

| Goal | Start here |
| --- | --- |
| Use the slower-changing `current` profile | `bash bin/latentcrate up current --detach` |
| Look up any command or flag | [CLI reference](docs/cli.md) |
| Use KJNodes' MiniMax H3 Sage patch | [SageAttention guide](docs/sageattention.md) |
| Add nodes with ComfyUI Manager or local-only nodes | [Third-party node guide](docs/third-party-nodes.md) |
| Find official workflows intended for local ComfyUI | [Template tools](docs/templates.md) |
| Download pinned files for an official workflow | [Model sets](docs/model-sets.md) |
| Develop or test a local node | [Local-only and private nodes](docs/third-party-nodes.md#local-only-and-private-nodes) |
| Test a public frontend fork or pull request | [Frontend modes](docs/frontends.md) |
| Build an uncommitted local frontend | [Local source mode](docs/frontends.md#local-source-mode) |
| Reuse an existing model library | [Storage layout](docs/storage.md) |
| Understand privacy and network access | [Privacy and containment](docs/privacy.md) |
| Update pinned versions | [Upgrade workflow](docs/upgrades.md) |
| Remove LatentCrate and free disk space | [Removal guide](docs/storage.md#removing-latentcrate-and-reclaiming-disk-space) |

## Is LatentCrate a good fit?

> **Project status:** community preview. See the
> [current validation status](docs/validation-status.md) for what has been
> validated on real hardware and what is still pending.

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

Pinning covers the components listed above. The base images and distribution
packages underneath can still change between rebuilds, so the full image is
not bit-for-bit reproducible.

The shipped version profiles (`current` and `edge`) are tuned for NVIDIA RTX
50-series cards. Other recent generations are expected to work after a
one-time profile edit, and an opted-in SageAttention build must support the
card's GPU architecture; the support matrix below shows the status, and the
[getting-started guide](docs/getting-started.md#first-launch) shows the exact
commands. AMD, ARM64, Jetson, native Windows containers, Kubernetes, and
public multi-user hosting are outside the current scope.

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

## Version profiles

A version profile is a checked-in set of ComfyUI, frontend, CUDA, PyTorch,
FFmpeg, SageAttention, and build-tool versions.

| Profile | Intended use | TorchCodec |
| --- | --- | --- |
| `current` | Slower-changing default | Disabled |
| `edge` | Newer pinned components for deliberate testing | Enabled with matching CUDA NPP runtime libraries |

Both are exact selections. Neither silently downloads `latest` or updates
itself when the container starts. The backend and frontend are pinned
independently, so a newer frontend can be tested with a stable backend. Show
the pinned values with `bash bin/latentcrate versions`; the
[configuration guide](docs/configuration.md) explains profile settings.

## Storage and privacy

Your work lives in host directories that you choose in `.env`:
`COMFY_DATA_DIR` holds workflows, user settings, inputs, outputs, and managed
nodes; `COMFY_MODELS_DIR` holds your model library; `COMFY_CACHE_DIR` holds
downloads and compiled caches. ComfyUI uses these paths directly, and
container recreation does not remove your workflows, outputs, models, or
Manager state. See the [storage layout](docs/storage.md).

The UI binds to `127.0.0.1` by default, and Manager sharing and direct
unreviewed install paths are disabled. These defaults reduce accidental
exposure; they do not make an unreviewed third-party node safe. Read
[privacy and containment](docs/privacy.md) before enabling remote access.

## Develop nodes and frontends

ComfyUI Manager installs third-party nodes from inside the UI. Their source
persists under `COMFY_DATA_DIR/custom_nodes`, and every `up` captures their
Python requirements into a rebuilt image layer instead of reinstalling them on
each start. Local or private nodes live below `local/custom_nodes`, mounted
read-only and excluded from Git and the image build. Commit-pinned node sets
such as `latent-nodepack` install a reviewed selection in one command. See the
[third-party node guide](docs/third-party-nodes.md).

The frontend is pinned independently of the backend and has four modes: the
pinned release, a trusted public Git fork, branch, or pull request, a local
source checkout built in a container, and a prebuilt `dist/` directory. This
separation lets you test frontend changes against a stable pinned backend.
See [frontend modes](docs/frontends.md).

## Verify a GPU setup

After the container is healthy, run `smoke-gpu` with the profile you started:

```bash
bash bin/latentcrate smoke-gpu edge
```

The command checks CUDA, Torch, Triton, FFmpeg codecs, and a short NVENC
hardware encode. With Sage enabled, it also checks SageAttention and a real
Sage kernel. It saves a report below `reports/`. Contributor checks that do not
need a GPU run with `bash tests/static.sh`.

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
