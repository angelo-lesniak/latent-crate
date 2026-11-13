# LatentCrate

Pinned ComfyUI containers for Docker and Podman.

LatentCrate runs ComfyUI in a container on an NVIDIA GPU. Every component
version is one line in [`pins.env`](pins.env): ComfyUI, its frontend, the
PyTorch and CUDA images, FFmpeg, TorchCodec, SageAttention. Nothing installs
or updates when the container starts, so the image you tested is the image
you run; a different version is a changed pin and a rebuild. Workflows,
models, and outputs stay in directories on your host that you choose.

Compared with a desktop ComfyUI installer, LatentCrate costs a long first
build and a container engine, and gives you pinned versions, third-party
node dependencies built into the image instead of installed at start, a
frontend you can swap or build from a fork, and verified model downloads for
official workflows.

LatentCrate is an unofficial community project. It is not affiliated with
Comfy Org, NVIDIA, Docker, or Podman.

## Start here

The host is x86-64 Linux (native, or inside WSL2) with Python 3.11 or
newer, Git, and one engine: Docker Engine with Compose v2, or rootless
Podman with `crun` and `podman-compose`. The NVIDIA driver major version
is at least `CUDA_MIN_DRIVER_MAJOR` in [`pins.env`](pins.env), and the
NVIDIA Container Toolkit has generated CDI devices (`doctor` prints the
`nvidia-ctk` command when they are missing).

The first build compiles FFmpeg and the node dependencies;
[Status](docs/validation.md#status) holds the measured build time.
`doctor` warns when the engine's
storage has less than 75 GB free. The `minimax-h3-i2v` model set downloads
about 44 GB into the models directory.

A GPU with a compute capability other than `12.0` (RTX 50-series) needs its
value in `COMFY_CUDA_ARCH_LIST` in `.env` before the build; see [GPU and
engine support](docs/configuration.md#gpu-and-engine-support) for how to
find the value and what else the host must provide.

```bash
git clone https://github.com/angelo-lesniak/latent-crate.git latentcrate
cd latentcrate
bash bin/latentcrate init
bash bin/latentcrate doctor
bash bin/latentcrate up --model-set minimax-h3-i2v
```

`init` creates a private `.env` and the data directories. `doctor` prints
one line per check; fix every `[fail]` line before `up`. `up` repeats the
checks, downloads the model set, builds the image, starts the container,
prints `LatentCrate is healthy at http://127.0.0.1:4207`, and follows the
ComfyUI logs. Open <http://127.0.0.1:4207>, open the template browser, and
choose the MiniMax H3 image-to-video template (`video_minimax_h3_i2v`), which
the set's files serve. Ctrl+C in the terminal stops the container.

If a step fails, [Troubleshooting](docs/troubleshooting.md) has one section
per message. For the next task, pick a row from the table below.

### Commands

```bash
bin/latentcrate init
bin/latentcrate doctor [--allow-no-gpu] [--sage mode]
bin/latentcrate up [--sage mode] [--detach] [--timeout seconds] [--model-set name]... [--frontend-dist dir | --frontend-source dir]
bin/latentcrate down
bin/latentcrate status
bin/latentcrate logs
```

`up` runs in order: the `doctor` checks, the fetch of each `--model-set`
(files already present and verified are skipped), the image build when an
input changed, the container start, a readiness wait bounded by
`--timeout` (default `300` seconds), then the logs. `--detach` returns
after readiness instead of following the logs. Ctrl+C during the wait or
the logs stops the container, which `down` then removes. `down` removes the
container; images and the data directories stay, and
[Removal](docs/configuration.md#removal) lists what else it leaves and what
is safe to delete.
`status` lists the project's containers; `logs` follows the
container. `bash bin/latentcrate help` prints every command.

## Choose what to do next

| Goal | Page |
| --- | --- |
| Find where your data lives, use another GPU, change a pin, or remove everything | [Configuration](docs/configuration.md) |
| Download the files an official workflow needs, or add a set | [Models](docs/models.md) |
| Add a custom node and make it load, or use SageAttention | [Nodes](docs/nodes.md) |
| Test a frontend fork or local frontend build against the pinned backend | [Frontends](docs/frontends.md) |
| Know what can leave the host, and lock the UI down | [Privacy](docs/privacy.md) |
| Fix a failing command from its message | [Troubleshooting](docs/troubleshooting.md) |
| See what is checked on real hardware and repeat the checks | [Validation](docs/validation.md) |

## Scope and support

LatentCrate targets x86-64 Linux hosts with an NVIDIA GPU, with Docker
Engine and Compose v2 or rootless Podman with `crun` and `podman-compose`.
Windows 10 and 11 provide that Linux host through WSL2; run the commands
inside the WSL2 distribution, not from PowerShell or Command Prompt. AMD
and other GPUs, ARM64, Jetson, native Windows containers, macOS,
Kubernetes, and multi-user hosting are out of scope.

[Status](docs/validation.md#status) states which platforms and features
have passed the validation procedures and which have not.

## Privacy

The UI binds to `127.0.0.1`, and the Hugging Face token reaches only the
Hub, from the host. ComfyUI Manager and third-party nodes run code you did
not review, inside the container but with your data mounted. [Privacy](docs/privacy.md)
says what runs where, what the defaults do not prevent, and how strict mode
closes the frontend's internet access.

## License

LatentCrate is available under the [MIT License](LICENSE). Container images also
include upstream software under its own licenses. Review
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before redistributing images.
