# Configuration

Settings live in `.env`, pins in `pins.env`, and your data in four host
directories that the container mounts. [Models](models.md#how-a-set-works)
and [Nodes](nodes.md#source-and-dependencies) cover what goes into two of
them.

## Settings

`.env.example` is the settings reference: every setting, its default, and
the command that applies a change. Precedence is a shell variable for one
command, then `.env`, then `.env.example`. `bash bin/latentcrate init`
creates `.env` with mode `0600`; the file must stay owned by the calling
user and readable by that user alone. Values are used literally: nothing
is expanded, and a comment stays on its own line. An `.env` key that
`.env.example` does not list is refused. See
[Troubleshooting](troubleshooting.md#the-env-file-is-not-private) for the
refusal texts and their remedies.

## Storage layout

| Setting | Container path | Mounted | Holds |
| --- | --- | --- | --- |
| `COMFY_DATA_DIR` | `/data` | writable | user settings, workflows, inputs, outputs, and the managed custom nodes under `custom_nodes` |
| `COMFY_MODELS_DIR` | `/models` | writable | the model library |
| `COMFY_CACHE_DIR` | `/cache` | writable | downloads, package caches, Triton, and temporary files |
| `COMFY_LOCAL_NODES_DIR` | `/local/custom_nodes` | read-only | private or unregistered node source |

A relative path is resolved from the checkout. No directory may be or
contain the checkout, and no two may overlap; every command refuses both.

[`config/comfy/extra_model_paths.yaml`](../config/comfy/extra_model_paths.yaml)
maps the subdirectories of `/models` to ComfyUI model categories; a model
set's `destination` names one of them. See
[Models](models.md#how-a-set-works) for the path from a manifest to a
category.

Local nodes sit outside `/data`, so Manager cannot edit them. Manager's
immediate installs land under the cache, outside the image, and are
disposable. See [Nodes](nodes.md#after-a-manager-change) for their path
and for what `up` captures.

A symbolic link below the models directory may point outside it.
`models fetch` writes through the link only when its target is listed in
`MODEL_SET_EXTRA_WRITE_ROOTS`; the container sees the target only when the
same path is bound inside it, and the Compose files bind the four
directories above and nothing else. A link to another disk therefore
serves `models fetch` but not ComfyUI; a host mount inside the models
directory serves both.

[Status](validation.md#status) holds the measured image sizes;
`doctor` warns when the engine storage is below its free-space floor.

## GPU and engine support

CDI is the only GPU selector. `GPU_DEVICE` names the CDI device,
`compose.gpu.yaml` requests it, and the image sets
`NVIDIA_VISIBLE_DEVICES=void` so no other runtime path injects a second
device set. A CDI specification for `nvidia.com/gpu` must exist where the
engine reads it: `/etc/cdi` or `/var/run/cdi` for Podman, the daemon's
CDI directories for Docker. `doctor` reports what it finds.

`CUDA_MIN_DRIVER_MAJOR` in `pins.env` is the driver floor; `doctor`
refuses an older driver.

`COMFY_CUDA_ARCH_LIST` is the list of CUDA architectures the SageAttention
and custom-node builds compile for. `doctor` prints the compute capability
of each GPU and refuses a card the list does not cover. Without the
wrapper, `nvidia-smi --query-gpu=compute_cap --format=csv,noheader`
prints it. The list is a build setting: change it, then `build`.

Podman runs rootless with `crun`; `keep-groups`, which passes the calling
user's supplementary groups into the container, requires `crun`. Docker
adds `HOST_MODEL_GID` as the supplementary group instead, the calling
user's group when unset, and its daemon needs CDI enabled in
`/etc/docker/daemon.json` with `"features": {"cdi": true}`.

`CONTAINER_ENGINE` fixes the engine; unset, Podman is tried first, then
Docker. Podman's Compose provider must be `podman-compose`: the Docker
Compose plugin drops CDI device requests. `up` records the engine in
`build/stack.json`; while the record exists every command uses that
engine and refuses a `CONTAINER_ENGINE` that names the other one, and
`down` removes the record.

Under WSL2 the engine client is the Linux one inside the distribution,
not a Windows `.exe` on the interop path. See
[Validation](validation.md#procedures) for the WSL2 differences.

## When a change takes effect

| Setting group | Applied by |
| --- | --- |
| Host directories | `init` creates them, `up` mounts them |
| Network, GPU, runtime | `up` |
| Build | `build` |
| Image variant | `build` and `up` |
| Engine | every command |
| `COMPOSE_PROJECT_NAME` | every command; run `down` before changing it |

`up` recreates the container on every start, so a runtime change needs no
`down` first. `.env.example` names the group of each setting.

## Pins and upgrading

`pins.env` is the pin reference: every pin with its meaning in the
comments beside it.

```text
bin/latentcrate build [--sage mode]
bin/latentcrate pins
bin/latentcrate pins check [component|all]
```

`build` copies every node's requirement files into
`build/custom-node-requirements`, builds the image for the Sage mode and
frontend mode in force, and starts nothing; it refuses `--frontend-source`
and `--frontend-dist`, which only `up` takes. `pins` prints the pins
through the validating loader, so a bad edit is a refusal. `pins check`
asks GitHub, GitLab, the npm registry, PyPI, and Docker Hub for the latest
eligible version of each pin and prints it next to the pinned one; it
writes nothing.

`PYTORCH_DEVEL_TAG`, `PYTORCH_RUNTIME_TAG`, `TORCHCODEC_VERSION`, and
`CUDA_NPP_VERSION` move together: they must name one PyTorch release and
CUDA family, and the loader refuses the file otherwise. Every `_TAG` pin
has its `_DIGEST` beside it, because the build pulls the digest.

One Sage mode, `LATENTCRATE_SAGE` or `--sage` for one command, selects
both the image target and the runtime flag, so `build`, `up`, and
`smoke-gpu` resolve the same image when they are given the same mode. See
[Nodes](nodes.md#sageattention) for what each mode does.

`COMPOSE_PROJECT_NAME` prefixes the container and network names only; the
image tag carries no project name. Two independent setups need separate
checkouts, storage paths, and `LATENTCRATE_IMAGE` values.

After a pin change run `build`, then `up`.

The entrypoint keeps the Triton and Torch extension caches per runtime
under `triton/<runtime>` and `torch-extensions/<runtime>` in
`COMFY_CACHE_DIR`. They are compilation caches that the next start fills
again, so deleting them while ComfyUI is stopped is safe; do so when a
kernel fails to load after a change to the image.

## Update the checkout

Run `git pull`, then `bash bin/latentcrate init` (it keeps an existing
`.env`), `doctor`, and `up`. A setting the update removed from
`.env.example` fails loudly, because an `.env` key the reference does not
list is refused.

## Removal

`down` stops and removes the project's containers, pods, and networks and
deletes `build/stack.json`. It leaves the images, the engine's build
cache, the host directories, `build/`, and `reports/`.

| Path | Safe to delete when |
| --- | --- |
| `COMFY_DATA_DIR` | you do not want its workflows, outputs, and managed nodes any more |
| `COMFY_MODELS_DIR` | you can fetch the sets again |
| `COMFY_CACHE_DIR` | ComfyUI is stopped |
| `COMFY_LOCAL_NODES_DIR` | never by this project; it is your source |
| `build/` (the lock, the stack record, the requirement copies, model-set drafts) | no stack runs and no command runs; `custom-node-requirements/.gitkeep` is tracked |
| `reports/` | any time |

Images are named
`latentcrate/comfy:<release|git|dist>[-sage]-<COMFYUI_REF>`, with
`LATENTCRATE_IMAGE` replacing the repository part.
`<engine> image ls latentcrate/comfy` lists them; removing them and
pruning the build cache are the engine's own commands, and the next `up`
builds again.
