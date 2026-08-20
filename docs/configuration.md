# Configuration

Most users only need to edit `.env`. It contains settings for one machine and is
not committed to Git.

Version profiles under `versions/` contain checked-in ComfyUI, frontend, CUDA,
PyTorch, FFmpeg, SageAttention, and build-tool selections.

Shell environment variables override `.env` and the selected version profile
for one command:

```bash
COMFY_PORT=4210 bash bin/latentcrate up current --detach
```

## Storage and host identity

| Setting | Default | Meaning |
| --- | --- | --- |
| `COMFY_DATA_DIR` | `./data/comfy` | User settings, workflows, inputs, outputs, and managed nodes |
| `COMFY_MODELS_DIR` | `./data/models` | Shared model library |
| `COMFY_CACHE_DIR` | `./data/cache` | Downloads, package caches, Triton, and temporary files |
| `COMFY_LOCAL_NODES_DIR` | `./local/custom_nodes` | Read-only private or unregistered node source |
| `HOST_UID` / `HOST_GID` | Set by `init` | Owner used for writable host files |
| `HOST_MODEL_GID` | Host GID | Extra model-storage group used by Docker |
| `UMASK` | `0002` | Permissions mask for created files |

`HF_TOKEN` is an optional Hugging Face read token for gated model sets. Put it
only in the uncommitted `.env`; the wrapper removes it from the environment
used by Compose. See
[Model sets](model-sets.md#hugging-face-access-and-licenses) for setup and
token handling.

`MODEL_SET_EXTRA_WRITE_ROOTS` is an advanced, colon-separated list of absolute
container paths that the model downloader may reach through model symlinks.
Each path still needs an explicit bind mount in the model helper services; this
setting never creates mounts.

Relative paths are resolved from the repository root. See
[storage layout](storage.md) for container paths and permission details.

## Engine, GPU, and network

| Setting | Default | Meaning |
| --- | --- | --- |
| `CONTAINER_ENGINE` | Auto-detected | `docker` or `podman` |
| `GPU_DEVICE` | `nvidia.com/gpu=all` | NVIDIA CDI device name |
| `NVIDIA_DRIVER_CAPABILITIES` | `compute,utility,video` | NVIDIA runtime capabilities |
| `COMFY_BIND_ADDRESS` | `127.0.0.1` | Host address used by the published UI port |
| `COMFY_PORT` | `4207` | Host UI port |
| `COMFY_ALLOW_REMOTE` | `false` | Required acknowledgement for a non-loopback bind |
| `COMPOSE_PROJECT_NAME` | `latentcrate` | Compose container and network name prefix |

Changing `COMPOSE_PROJECT_NAME` does not isolate images, saved dependency
snapshots, or caches. For two fully independent setups, use separate checkouts,
storage paths, and `LATENTCRATE_IMAGE`/`LATENTCRATE_TOOLS_IMAGE` names.

Podman must be rootless and use `crun`. The wrapper also selects Docker image
format for Podman builds because the Dockerfile relies on Bash `SHELL` metadata.

## SageAttention

`LATENTCRATE_SAGE` (default `available`) selects one of three Sage modes; the
modes and the per-command `--sage <mode>` override are defined in
[shared variant flags](cli.md#shared-variant-flags).

The default gives workflows access to SageAttention without forcing global
replacement. Use `global` only after representative workflows have been tested
with global Sage. See [SageAttention](sageattention.md).

## ComfyUI behavior

| Setting | Default | Meaning |
| --- | --- | --- |
| `COMFY_ENABLE_MANAGER` | `true` | Enable ComfyUI Manager |
| `COMFY_DISABLE_API_NODES` | `false` | Strict mode: disable external API nodes and frontend internet access |
| `COMFY_DISABLE_METADATA` | `false` | Stop embedding prompts and workflows in generated files |
| `COMFYUI_EXTRA_ARGS` | Empty | Extra whitespace-separated ComfyUI flags |
| `RESTART_POLICY` | `no` | Compose restart policy |
| `STOP_GRACE_PERIOD` | `30s` | Graceful shutdown time |
| `COMFY_SHM_SIZE` | `8gb` | Shared-memory size |
| `COMFY_TMPFS_SIZE` | `1g` | Writable `/tmp` size |
| `COMFY_PIDS_LIMIT` | `2048` | Runtime process limit |

`COMFYUI_EXTRA_ARGS` is an advanced setting for flags not covered elsewhere.
Paths containing spaces are not supported in this value.

`RESTART_POLICY=no` prevents a computer restart from unexpectedly reserving the
GPU. Use `unless-stopped` when automatic restart is wanted.

For node dependency behavior, Git requirements, private nodes, and pinned node
sets, see [third-party nodes](third-party-nodes.md).

Every `up` and `build` saves and rebuilds node dependencies by default. The
`--use-saved-node-deps` flag is described in the [CLI reference](cli.md#up).

## Frontend selection

Prefer command-line options for temporary frontend work; they are listed in
the CLI reference under [frontend flags](cli.md#frontend-flags). For a saved
configuration, use:

| Setting | Meaning |
| --- | --- |
| `COMFY_FRONTEND_MODE` | `release`, `git`, `source`, or `dist` |
| `FRONTEND_GIT_URL` | Public HTTPS repository used by Git mode |
| `FRONTEND_GIT_REF` | Commit, branch, tag, or pull-request reference |
| `COMFY_FRONTEND_SOURCE_DIR` | Local source checkout built in containers |
| `COMFY_FRONTEND_DIST_DIR` | Existing built frontend files mounted read-only |

Each frontend flag selects the matching `COMFY_FRONTEND_MODE` for one command.
See [frontend modes](frontends.md) before using source that you do not control.

## Version profiles

A profile is a shell-compatible `.env` file under `versions/`. Copy an existing
profile before experimenting:

```bash
cp versions/current.env versions/my-gpu.env
# For another GPU, edit CUSTOM_NODE_CUDA_ARCH_LIST and SAGE_CUDA_ARCH_LIST.
# Edit LATENTCRATE_TAG to my-gpu-sage so it matches the new profile name.
bash bin/latentcrate doctor my-gpu
bash bin/latentcrate up my-gpu --detach
```

`LATENTCRATE_TAG` names the image that the profile builds; it must be the
profile file name plus `-sage`. The project checks enforce this for every file
under `versions/`.

Important build selections include:

- `PYTORCH_DEVEL_IMAGE` and `PYTORCH_RUNTIME_IMAGE`;
- `COMFYUI_REF` and `COMFYUI_FRONTEND_REF`;
- `FFMPEG_REF`, codec-library references, and `CUDA_NPP_VERSION`;
- `SAGEATTENTION_REF`, `SAGE_CUDA_ARCH_LIST`, and `SAGE_BUILD_JOBS`;
- `CUSTOM_NODE_CUDA_ARCH_LIST`;
- `TORCHCODEC_VERSION` and `TORCHCODEC_INDEX_URL`;
- `FRONTEND_NODE_IMAGE`, `FRONTEND_PNPM_VERSION`, and `TOOL_PYTHON_IMAGE`;
- `CUDA_MIN_DRIVER_MAJOR`.

Change one dependency group at a time. `SAGE_BUILD_JOBS=2` is a low-memory
default for typical 16 GB builders. Raise it only when the builder has enough
measured free memory.

### TorchCodec and CUDA NPP

An empty `TORCHCODEC_VERSION` disables TorchCodec for that profile. In the
shipped profiles, `current` disables it and `edge` enables it. A non-empty value
must select the CUDA-specific wheel that matches PyTorch.

TorchCodec's CUDA video path also needs parts of NVIDIA Performance Primitives
(NPP). When TorchCodec is enabled, the build copies the matching `libnppc` and
`libnppicc` runtime libraries from the selected CUDA development image. It does
not copy NVCC, CUDA headers, or the full CUDA toolkit. The NVIDIA driver still
comes from the host when the container starts.

Treat `TORCHCODEC_VERSION`, `TORCHCODEC_INDEX_URL`, `CUDA_NPP_VERSION`, and the
two PyTorch images as one compatibility group. Update them together, then test
CPU decoding during the build and a representative CUDA video workflow on an
NVIDIA host.

The project uses maintained container engines and Compose providers. It does not
promise support for old versions. The versions in
[validation status](validation-status.md) show what one test run used; they are
not guaranteed minimum versions. CI also renders every Compose variant and
generates all wrapper command shapes with podman-compose 1.6.0, without treating
that exact version as a permanent minimum.

## Advanced: image names and tags

These settings control how the locally built images are named. Most users never
change them.

| Setting | Default | Meaning |
| --- | --- | --- |
| `LATENTCRATE_IMAGE` | `latentcrate/comfy` | Name of the ComfyUI runtime image |
| `LATENTCRATE_TAG` | Set by the wrapper | Runtime image tag: the profile name plus variant suffixes, for example `current-sage` or `edge-frontend-git-sage` |
| `LATENTCRATE_TOOLS_IMAGE` | `latentcrate/tools` | Name of the helper-tool images (dependency snapshot, node sets, frontend builds) |
| `LATENTCRATE_TOOLS_TAG` | Set by the wrapper | Tool image tag prefix: the profile name; each tool appends its own suffix (`-node-deps`, `-node-set`, `-model-set`, `-frontend`) |

The wrapper derives `LATENTCRATE_TAG` and `LATENTCRATE_TOOLS_TAG` from the
selected profile and options. Do not override the tags in `.env` or on the
command line. The `LATENTCRATE_TAG` line inside a version profile file is
different: it must be present and match the profile file name plus `-sage`,
which is why the copied-profile recipe tells you to edit it there. Override
`LATENTCRATE_IMAGE` and `LATENTCRATE_TOOLS_IMAGE` only to keep two fully
independent checkouts from sharing images.

## Configuration safety

The wrapper reads `.env` as a Bash file. Treat it as trusted local code, use
shell-compatible quoting, and do not copy an unreviewed `.env` from another
person.

Use `bash bin/latentcrate ...` as the supported interface. Direct Compose calls
need internal variables and engine overlays and skip important wrapper checks.
