# Troubleshooting

One section per observed failure, with cause and remedy.
`bash bin/latentcrate doctor` repeats every host
check; `bash bin/latentcrate logs` follows the container.

## The `.env` file is not private

```text
<path> must be owned by your user (uid <uid>), since it may hold credentials
<path> must be readable by your user only; run: chmod 600 <path>
```

`.env` may hold `HF_TOKEN` and `CIVITAI_TOKEN`, so another owner or a
wider mode is refused. Run the `chmod`, or `chown` the file to yourself.

## `.env` sets a key `.env.example` does not list

```text
.env sets <keys>, which .env.example does not list; remove the line or set the variable in the shell instead
```

The key is a typo, a removed setting, or an engine variable. Remove the
line or export the variable in the shell. See
[Settings](configuration.md#settings) for the precedence.

## No CDI spec was found

```text
no nvidia.com/gpu CDI spec was found under /etc/cdi or /var/run/cdi; run: sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
```

The NVIDIA Container Toolkit has not generated a CDI specification where
the engine reads it. Run the printed command. See
[GPU and engine support](configuration.md#gpu-and-engine-support) for the
CDI selector and where the engine reads the spec.

## Docker reports no CDI spec directories

```text
Docker reports no CDI spec directories; enable CDI in /etc/docker/daemon.json ("features": {"cdi": true}) or upgrade Docker so <GPU_DEVICE> can resolve
```

The Docker daemon runs without CDI. Add the feature to `daemon.json` and
restart the daemon. See
[GPU and engine support](configuration.md#gpu-and-engine-support) for the
Docker requirements.

## The arch list does not cover the GPU

```text
COMFY_CUDA_ARCH_LIST=<list> does not cover compute capability <capability>; add it to the list in .env
```

The build compiles for the listed architectures only. Add the printed
capability to `COMFY_CUDA_ARCH_LIST` in `.env` and run `build`. See
[GPU and engine support](configuration.md#gpu-and-engine-support) for the
arch list setting.

## The driver is older than the pinned minimum

```text
NVIDIA driver <version> is older than the pinned minimum major <major> (CUDA_MIN_DRIVER_MAJOR)
```

The pinned CUDA family needs a newer host driver. Update the driver. See
[Pins and upgrading](configuration.md#pins-and-upgrading) for the pin.

## Neither engine is usable

```text
neither Podman nor Docker has both a reachable engine and Compose provider; run: bash bin/latentcrate doctor
```

No engine passed every probe. `doctor` prints one line per probe of each
engine it found; fix the first failed one. See
[GPU and engine support](configuration.md#gpu-and-engine-support) for the
Podman and Docker requirements.

## Rootless Podman with keep-groups requires crun

```text
rootless Podman with keep-groups requires crun (detected: <runtime>); set runtime = "crun" under [engine] in ~/.config/containers/containers.conf
```

`keep-groups` passes your supplementary groups into the container and
only `crun` honours it. Set the runtime as printed, then `doctor`. See
[GPU and engine support](configuration.md#gpu-and-engine-support) for
the Podman requirements.

## The Podman compose provider is the Docker Compose plugin

```text
the Podman compose provider is the Docker Compose plugin, which drops CDI GPU devices; install podman-compose
```

`podman compose` delegates to a provider that loses the GPU request.
Install `podman-compose`. See
[GPU and engine support](configuration.md#gpu-and-engine-support) for the
engine selection.

## The stack runs on another engine

```text
the stack runs on <recorded>, not <requested> (build/stack.json); run: CONTAINER_ENGINE=<recorded> bash bin/latentcrate down
```

A running stack holds the GPU and the port on the recorded engine. Run
the printed `down` first. See
[GPU and engine support](configuration.md#gpu-and-engine-support) for the
stack record.

## WSL must use a Linux client

```text
WSL must use a Linux <engine> client inside the distribution, not <path>
```

The Windows `.exe` on the interop path cannot run the stack. Install the
engine inside the distribution. See
[WSL2 differences](validation.md#wsl2-differences).

## Cannot use the host port

```text
cannot use host port <port> on <address>: <cause>
```

Another program holds the port; under WSL2 `doctor` appends `; under WSL2
a Windows-side program can hold a port invisibly to Linux tools (check
with netstat -ano from Windows)`. Stop that program or set another
`COMFY_PORT`. See [Settings](configuration.md#settings) for overriding a
setting for one command.

## The container is not ready

```text
container is <state> before it was ready. Check the logs: bash bin/latentcrate logs
container did not become healthy within <timeout>s. Check the logs: bash bin/latentcrate logs
```

ComfyUI exited or did not answer on the published address in time. Run
`bash bin/latentcrate logs` and fix the first error it shows. See
[Start here](../README.md#start-here) for what `up` does and its
`--timeout`.

## A directory is not writable

```text
LatentCrate: directory is not writable: <path>
```

The container exits before it is ready: the container user cannot write
a mounted directory, on Docker usually for lack of its group. Set
`HOST_MODEL_GID` as `.env.example` says, then `up`. See
[GPU and engine support](configuration.md#gpu-and-engine-support) for
how each engine grants the group.

## The selected image does not exist locally

```text
selected image does not exist locally: <image>; run: bash bin/latentcrate build
```

`smoke-gpu` resolved an image that was never built for this Sage and
frontend selection. Run `build` with the same `--sage` mode, or pass the
mode the stack runs with. See
[Pins and upgrading](configuration.md#pins-and-upgrading).

## The running container uses another image

```text
the running container uses another image than <image>; rerun up with the same --sage and frontend selection (or run down first)
```

`smoke-gpu` was given a different `--sage` mode or frontend mode than
`up`. Repeat the mode `up` ran with. See
[Dist mode](frontends.md#dist-mode) for smoking a stack started with a
frontend flag.

## SageAttention was required but the container is not Sage-capable

```text
LatentCrate: SageAttention was required, but the running container is not Sage-capable.
Recreate it with a Sage-capable image: bash bin/latentcrate up --sage available --detach
```

`smoke-gpu --sage available` or `global` found a container from the image
without SageAttention. Run the printed `up`. See
[SageAttention](nodes.md#sageattention).

## A custom-node set is missing nodes

```text
custom-node set <name> is missing <nodes>; run: bash bin/latentcrate nodes install <name>
```

`up` found the set's record but not every checkout. Run the printed
install. See [Node sets](nodes.md#node-sets).

## Stop ComfyUI before installing a node set

```text
stop ComfyUI before installing or syncing a custom-node set: bash bin/latentcrate down
```

The image is built from these sources, so they change only while the
stack is down. Run `down`, then the install. See
[Node sets](nodes.md#node-sets).

## Cached runtime packages have dependency conflicts

```text
LatentCrate: cached runtime packages have dependency conflicts.
Rebuild the image (bash bin/latentcrate build), then clear <PYTHONUSERBASE>.
```

Packages Manager installed into the cache disagree with the image. Run
`build`, then delete the printed directory under `COMFY_CACHE_DIR`. See
[After a Manager change](nodes.md#after-a-manager-change).

## The image is not built

```text
the image <image> is not built; run: bash bin/latentcrate build
```

`templates` runs inside the image and never builds it. Run `build`. See
[Add a set](models.md#add-a-set) for the templates tools.

## Cannot access a gated repository

```text
cannot access <repository>/<file> at <revision> (HTTP 401). For gated repositories, accept the license and set HF_TOKEN in .env.
```

The code is `401` or `403`. Accept the license on Hugging Face and set
`HF_TOKEN` in `.env`. See [Fetch a set](models.md#fetch-a-set).

## Civitai serves the file to signed-in accounts only

```text
cannot access Civitai model version <version> (HTTP 401). Civitai serves it to signed-in accounts only: set CIVITAI_TOKEN in .env.
```

The code is `401` or `403`. Set `CIVITAI_TOKEN` in `.env`. See
[Civitai sources](models.md#civitai-sources).

## A Civitai version has no single primary file

```text
Civitai model version <version> has no single primary file; name one as civitai_file: <id> '<name>' (<size> bytes), ...
```

Civitai marks none or several of the version's files primary. Set
`civitai_file` to the id of the wanted file in the list. See
[Manifest keys](models.md#manifest-keys).

## A file has other content

```text
<destination> has the <state>; move it aside before downloading the pinned file
```

A file at the destination differs from the pinned one, and the downloader
never overwrites. Move it aside and fetch again. See
[Fetch a set](models.md#fetch-a-set).

## A download stopped

```text
download of <destination> stopped after <received> of <size> bytes; run models fetch again to resume
download of <destination> stopped after <received> of <size> bytes; run models fetch again to start over
```

The connection ended. Run `models fetch` again; it resumes the partial
file, or restarts a private file without `size`. See
[Fetch a set](models.md#fetch-a-set).

## The selected files need more free space

```text
the selected files need <needed> GB but only <free> GB are free below <models directory>; make room or fetch fewer sets
```

The bytes still to download exceed the free space. Make room or fetch
fewer sets. See [Storage layout](configuration.md#storage-layout).

## Another download is using the models directory

```text
another model-set download is using this models directory
```

One `models fetch` runs per models directory. Wait for it, or stop it.
See [Fetch a set](models.md#fetch-a-set).

## The destination escapes the allowed model storage

```text
destination escapes the allowed model storage: <destination>; list its symlink target in MODEL_SET_EXTRA_WRITE_ROOTS
```

A symbolic link below the models directory points to a directory not
listed in `MODEL_SET_EXTRA_WRITE_ROOTS`. List the target. See
[Storage layout](configuration.md#storage-layout) for the link rule.

## The frontend reference is ambiguous

```text
frontend reference <ref> is ambiguous; write refs/heads/<ref> or refs/tags/<ref>
```

A branch and a tag carry the same name. Write the full reference. See
[Git mode](frontends.md#git-mode).

## Asking for help

Open an issue in the
[issue tracker](https://github.com/angelo-lesniak/latent-crate/issues);
for a vulnerability, follow [SECURITY.md](../SECURITY.md) instead. Attach
the output of `bash bin/latentcrate pins`, `doctor`, and `status`, the
output of the failing command, the report path `smoke-gpu` printed, and
the engine and driver versions. Remove private paths, prompts, and
workflows, and never include `HF_TOKEN` or `CIVITAI_TOKEN`.
