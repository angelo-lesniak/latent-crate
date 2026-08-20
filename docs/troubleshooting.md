# Troubleshooting

Start with these two commands:

```bash
bash bin/latentcrate doctor current
bash bin/latentcrate logs current
```

Use the same version profile that you used when starting the container. Repeat
`--sage off` with `doctor`, `config`, or `smoke-gpu` when you started the
opt-out image. The sections below cover common first-run problems.

## `doctor` cannot find an NVIDIA CDI device

**Likely cause:** NVIDIA Container Toolkit is missing, its CDI file is stale, or
`GPU_DEVICE` names a device that is not present.

```bash
nvidia-smi
nvidia-ctk cdi list
```

Follow the current NVIDIA Container Toolkit setup for your engine, regenerate
CDI devices, and check `GPU_DEVICE` in `.env`. Avoid using CDI and an older
NVIDIA OCI hook at the same time. Run `bash bin/latentcrate doctor current`
again and confirm the CDI check no longer fails.

## The CUDA architecture check fails

**Likely cause:** the version profile targets compute capability 12.0
(RTX 50-series), but your GPU has another capability.

Show your card's capability. Plain `nvidia-smi` does not display it; use the
query form:

```bash
nvidia-smi --query-gpu=compute_cap --format=csv,noheader
```

Copy `versions/current.env` to a new profile and change both architecture
lists to that value. The
[getting-started guide](getting-started.md#first-launch) shows the exact
commands. Native packages must be rebuilt for the selected capability.
Complete the relevant GPU validation guide before describing that profile as
validated.

## Docker or Podman is not selected correctly

**Likely cause:** both engines are installed and automatic selection picked
the other one, or the selected engine has no working Compose provider.

Set one engine in `.env`:

```dotenv
CONTAINER_ENGINE=docker
```

or:

```dotenv
CONTAINER_ENGINE=podman
```

Podman must be rootless, use `crun`, and have a working Compose provider. In
WSL2, use a Linux client inside the distribution. A forwarded Windows `.exe`
client is not supported. Run `bash bin/latentcrate doctor current` again and
confirm that no `[fail]` line mentions the engine or Compose.

## The first build runs out of disk or memory

The CUDA bases, FFmpeg stages, Sage compilation, and build cache are large.
Keep at least 75 GB free for a first release build and more when building several
profiles. Sage uses a low-memory default of two compiler jobs. Reduce
`SAGE_BUILD_JOBS` in the selected profile if the compiler is killed for memory.

If Sage is not needed for a diagnostic build, use `--sage off`. The mode is an
opt-out from LatentCrate's normal feature set, not a separate compatibility
mode.

## The UI does not become healthy

Follow the logs:

```bash
bash bin/latentcrate logs current
```

Check for a missing model/data mount, a custom-node import error, an invalid
Manager configuration, or another program already using port `4207` (see
[the port is already in use](#the-port-is-already-in-use)). Inspect service
state with:

```bash
bash bin/latentcrate status current
```

## The port is already in use

**Symptom:** startup fails and the engine reports an error such as
`port is already allocated` or `address already in use`. Another program on
the host already holds port `4207`.

Set a different host port in `.env`:

```dotenv
COMFY_PORT=4210
```

Or override it for one command; shell environment variables take precedence
over `.env`:

```bash
COMFY_PORT=4210 bash bin/latentcrate up current --detach
```

> **Note:** inside WSL2, a Windows program can hold the port. Linux-side tools,
> including `doctor`, cannot see it. Check from Windows with
> `netstat -ano | findstr 4207` in PowerShell or Command Prompt.

## Permission denied under data, models, or cache

Run `bash bin/latentcrate init` again; it keeps `.env` but prepares missing
directories. Confirm `HOST_UID` and `HOST_GID` match `id -u` and `id -g`.

For a group-owned model library, Docker can add `HOST_MODEL_GID`. Rootless
Podman uses the current user's supplementary groups through `keep-groups` and
therefore requires `crun`.

## A third-party node cannot import a Python package

Manager's immediate runtime install is disposable. Save the node requirements
in the image and recreate the container:

```bash
bash bin/latentcrate up current --detach
bash bin/latentcrate wait current
```

If the build fails, the previous buildable dependency snapshot is restored.
Read the build error before adding a constraint or Dockerfile package. See the
[third-party node guide](third-party-nodes.md).

## A frontend change is not visible

Repeat the same frontend option used for the previous start. For local source:

```bash
bash bin/latentcrate up edge \
  --frontend-source /path/to/ComfyUI_frontend \
  --detach
```

LatentCrate rebuilds the frontend files and recreates the container. For a
public branch or pull request, it resolves the current commit before the image
build. See [frontend modes](frontends.md).

## SageAttention is installed but a workflow does not use it

The Sage-capable image does not force global replacement. Select the Sage option
inside the workflow or use the KJNodes patch required by that workflow.

Set `LATENTCRATE_SAGE=global` only after representative workflows pass with
ComfyUI's global `--use-sage-attention` behavior.

## WSL2 bind mounts are slow

Move the checkout, data, and cache from `/mnt/c/...` to the WSL Linux
filesystem, such as `~/src/latentcrate` and `~/latentcrate-data`. Keep model
storage close to the engine when possible.

## Asking for help

Open an issue in the
[LatentCrate issue tracker](https://github.com/angelo-lesniak/latent-crate/issues).
For security problems, follow [SECURITY.md](../SECURITY.md) instead of opening
a public issue. Include:

- the exact command that failed;
- the selected version profile and engine;
- relevant `doctor` and log output;
- GPU and driver information from `nvidia-smi`;
- a GPU report from `reports/` when `smoke-gpu` ran.

Remove host paths, prompts, workflow data, and other private information before
sharing logs or reports.
