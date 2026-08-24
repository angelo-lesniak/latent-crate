# CLI reference

This page documents every `bin/latentcrate` command and flag. Run every
command as `bash bin/latentcrate <command> ...`. The wrapper (the
`bin/latentcrate` script) is the supported interface; direct Compose calls
skip its checks.

General rules:

- `[profile]` is a version profile: a file under `versions/` without the
  `.env` suffix. It defaults to `current` everywhere.
- The wrapper loads `.env` first. Shell environment variables override `.env`
  for one command, for example
  `COMFY_PORT=4210 bash bin/latentcrate up current --detach`.
- `CONTAINER_ENGINE=docker` or `CONTAINER_ENGINE=podman` overrides engine
  detection. Podman must be rootless and use `crun`.
- `LATENTCRATE_SAGE` (default `available`) selects the Sage mode. The
  command-line flag `--sage <mode>` overrides it for one command.

## Command summary

| Command | Purpose |
| --- | --- |
| `help` | Show usage |
| `init` | Create `.env` and the host directories; optionally install a node set |
| `doctor` | Check the host before a large build |
| `up` | Capture node dependencies, build the image, and start ComfyUI |
| `build` | Capture node dependencies and build the image without starting |
| `down` | Stop and remove the ComfyUI container |
| `config` | Print the rendered Compose configuration |
| `status` | Show the ComfyUI service state |
| `wait` | Wait until the running container reports healthy |
| `logs` | Follow the ComfyUI logs |
| `shell` | Open a Bash shell inside the running container |
| `smoke-gpu` | Run the real GPU and media checks and save a report |
| `frontend pin-release` | Refresh a profile's pinned frontend release archive digest |
| `node-deps snapshot` | Capture third-party node requirements without building the runtime image |
| `node-deps clear` | Reset the saved third-party node requirements |
| `nodes list` | List the available commit-pinned node sets |
| `nodes install` | Install a node set into `COMFY_DATA_DIR/custom_nodes` |
| `nodes sync` | Update an installed node set to its pinned commits |
| `nodes status` | Compare an installed node set with its manifest |
| `models list` | List the available pinned model-file sets |
| `models fetch` | Download and verify one or more model sets |
| `models status` | Verify selected model files without network access |
| `templates list` | List local-compatible official templates in a selected image |
| `templates create-model-set` | Create a model-set manifest draft from a template |
| `versions` | Print the pinned values of every version profile |

## Shared variant flags

`doctor`, `up`, `build`, `config`, and `smoke-gpu` accept
`--sage <off|available|global>`:

| Mode | Effect |
| --- | --- |
| `off` | Smaller image without SageAttention |
| `available` | Sage-capable image; workflows opt in (the default) |
| `global` | Sage-capable image; ComfyUI applies Sage globally (`--use-sage-attention`) |

One mode drives both the image variant and the runtime behavior, so an
impossible combination cannot be selected, and `doctor`, `up`, `build`,
`config`, and `smoke-gpu` always resolve the same variant for the same mode.
`doctor` performs the same checks for `available` and `global`. Use `global`
only after representative workflows pass with global Sage; see
[SageAttention](sageattention.md).

## Frontend flags

At most one frontend flag may be supplied per command.

| Flag | Arguments | Valid with | Meaning |
| --- | --- | --- | --- |
| `--frontend-release` | none | `up`, `build`, `config`, `smoke-gpu` | Use the release frontend pinned by the profile (the default) |
| `--frontend-git` | `<https-url> <reference>` | `up`, `build`, `config`, `smoke-gpu` | Build a public fork; the reference is a commit, branch, tag, or pull-request reference |
| `--frontend-source` | `<source-directory>` | `up`, `config`, `smoke-gpu` | Build an uncommitted local checkout in containers (local source mode) |
| `--frontend-dist` | `<dist-directory>` | `up`, `config`, `smoke-gpu` | Mount an existing built `dist/` directory read-only |

`build` supports only the release and Git frontends; use `up` for
`--frontend-source` or `--frontend-dist`.

## init

```text
bin/latentcrate init [--node-set name] [profile]
```

Creates `.env` from `.env.example` when `.env` does not exist, fills in
`HOST_UID`, `HOST_GID`, and `HOST_MODEL_GID` from the current user, and creates
the host storage directories. An existing `.env` keeps its content, but every
run re-applies mode `0600`, so re-running `init` is the supported way to fix
`.env` permissions.

| Flag | Meaning |
| --- | --- |
| `--node-set <name>` | Also install the named commit-pinned node set for the selected profile |

```bash
bash bin/latentcrate init
bash bin/latentcrate init --node-set latent-nodepack current
```

## doctor

```text
bin/latentcrate doctor [profile] [--allow-no-gpu] [--sage mode]
```

Checks the host: engine and Compose availability, GPU and driver, CDI devices,
whether the profile's CUDA architecture lists cover the detected compute
capability, about 75 GB of free engine storage, the existence and permissions
of the host storage directories, and whether `COMFY_PORT` is already in use.
Fix every `[fail]` line before building.

| Flag | Meaning |
| --- | --- |
| `--allow-no-gpu` | Do not fail when no NVIDIA GPU is available (for CPU-only checks) |

```bash
bash bin/latentcrate doctor current
bash bin/latentcrate doctor my-gpu --sage off
```

## up

```text
bin/latentcrate up [profile] [--sage mode] [--detach] [--use-saved-node-deps] [--model-set name] [frontend flag]
```

Captures the current third-party node requirements, builds the image, and starts
ComfyUI. If the dependency capture or build fails, the last buildable
dependency snapshot is restored. After a successful build, `up` recreates the
ComfyUI container so it always runs the image that was just built.

| Flag | Meaning |
| --- | --- |
| `--detach`, `-d` | Start in the background; follow up with `wait` |
| `--use-saved-node-deps` | Skip the dependency refresh for this command and build from the last saved requirements |
| `--model-set <name>` | Download and verify this model set before building and starting; repeat for more sets |

```bash
bash bin/latentcrate up current --detach
bash bin/latentcrate up edge --frontend-source /path/to/ComfyUI_frontend --detach
```

## build

```text
bin/latentcrate build [profile] [--sage mode] [--use-saved-node-deps] [frontend flag]
```

Same capture and image build as `up`, but does not start ComfyUI. Supports the
release and Git frontends only.

```bash
bash bin/latentcrate build current
bash bin/latentcrate build edge --use-saved-node-deps
```

## down

```text
bin/latentcrate down [profile]
```

Stops and removes the ComfyUI container and its Compose network. Images, build
cache, and host data are kept; see
[removing LatentCrate](storage.md#removing-latentcrate-and-reclaiming-disk-space).

```bash
bash bin/latentcrate down current
```

## config

```text
bin/latentcrate config [profile] [--sage mode] [frontend flag]
```

Prints the fully rendered Compose configuration for inspection. Repeat the
same variant and frontend flags that you use with `up`.

```bash
bash bin/latentcrate config current --sage off
```

## status

```text
bin/latentcrate status [profile]
```

Shows the ComfyUI service state (`compose ps`).

```bash
bash bin/latentcrate status current
```

## wait

```text
bin/latentcrate wait [profile] [--timeout seconds]
```

Waits until the running container reports healthy, then prints the UI address.
Fails if the container becomes unhealthy, exits, or the timeout passes.

| Flag | Meaning |
| --- | --- |
| `--timeout <seconds>` | Maximum wait time as a positive integer; default `180` |

```bash
bash bin/latentcrate wait current
bash bin/latentcrate wait current --timeout 600
```

## logs

```text
bin/latentcrate logs [profile]
```

Follows the ComfyUI container logs until interrupted.

```bash
bash bin/latentcrate logs current
```

## shell

```text
bin/latentcrate shell [profile]
```

Opens an interactive Bash shell inside the running ComfyUI container. The
container must already be running.

```bash
bash bin/latentcrate shell current
```

## smoke-gpu

```text
bin/latentcrate smoke-gpu [profile] [--sage mode] [frontend flag]
```

Runs the real GPU and media checks inside the running container: CUDA, Torch,
Triton, SageAttention, FFmpeg codecs, a Sage kernel, and a short NVENC encode.
Saves a report below `reports/`. Repeat the same variant and frontend flags
used for `up`; the command verifies that the running container matches the
selected image.

```bash
bash bin/latentcrate smoke-gpu current
bash bin/latentcrate smoke-gpu edge --frontend-git https://github.com/your-name/ComfyUI_frontend.git your-branch
```

## frontend pin-release

```text
bin/latentcrate frontend pin-release [profile]
```

Reads `COMFYUI_FRONTEND_REF` from the selected `versions/*.env` profile, then
uses a short-lived Compose helper to download that exact release's `dist.zip`
into tmpfs and validate the archive. The helper has no host bind mounts and
prints the SHA-256 for the wrapper to validate and atomically write to
`COMFY_FRONTEND_DIST_SHA256`. The archive disappears with the container. The
profile is not changed after a failed download or invalid helper output. Only
one pin command can run for a profile at a time. Do not edit that profile until
the command finishes. The wrapper checks for edits before publication and
aborts when it detects one; the final rename prevents a partially written
profile but cannot coordinate an editor that does not use the command's lock.

Run this after changing a profile's frontend release reference:

```bash
bash bin/latentcrate frontend pin-release edge
```

## node-deps

```text
bin/latentcrate node-deps snapshot [profile]
bin/latentcrate node-deps clear
```

`snapshot` captures the Python requirements of the managed and local custom
nodes into `build/custom-node-requirements` without building the runtime
image. Rebuild with `up` or `build` afterward to install them.

`clear` resets the saved requirements to empty. The next refresh creates a new
snapshot from the current nodes. `clear` accepts no profile argument.

```bash
bash bin/latentcrate node-deps snapshot current
bash bin/latentcrate node-deps clear
```

## nodes

```text
bin/latentcrate nodes list
bin/latentcrate nodes install <set> [profile]
bin/latentcrate nodes sync <set> [profile]
bin/latentcrate nodes status <set> [profile]
```

Manages commit-pinned node sets from `config/custom-nodes/sets/`. A node set
is a named group of third-party node repositories pinned to exact commits.
`install` adds the set, `sync` updates a clean installation to the pinned
commits, and `status` compares the installation with the manifest. Stop
ComfyUI before `install` or `sync`; the wrapper checks this. See
[third-party nodes](third-party-nodes.md).

```bash
bash bin/latentcrate nodes list
bash bin/latentcrate nodes install latent-nodepack current
```

## models

```text
bin/latentcrate models list
bin/latentcrate models fetch [--profile profile] <set> [<set> ...] | all
bin/latentcrate models status [--profile profile] <set> [<set> ...] | all
```

`fetch` downloads pinned Hugging Face files into `COMFY_MODELS_DIR`, verifies
their size and SHA-256 checksum, then publishes them atomically. Several sets
may be named in one command; shared files are downloaded once. `all` selects
every shipped set. `status` performs the same local verification without
network access or file changes.

The helper image follows the selected version profile; use `--profile edge`
when wanted. The profile does not change which files a model set contains.

```bash
bash bin/latentcrate models list
bash bin/latentcrate models fetch flux2-klein-9b-distilled
bash bin/latentcrate models status --profile edge all
```

See [Model sets](model-sets.md) for licenses, token handling, included
workflows, and storage behavior.

## templates

```text
bin/latentcrate templates list [profile]
bin/latentcrate templates create-model-set <template> [profile] [--name name]
```

Both subcommands first build the selected image when it is not built yet. A
first build has the full time, download, and disk cost stated in the README
(up to several hours and about 75 GB of build space) and needs network access;
only the inspection itself runs offline. The `templates` commands take no
`--sage` flag; they follow `LATENTCRATE_SAGE` from `.env` so they inspect the
image variant you normally run.

`list` reads the official workflow-template package installed in the selected
ComfyUI image and shows templates marked as open source and suitable for local
distribution. It excludes archived, deprecated, cloud-only, and packaged API
workflows. This offline metadata check does not prove that extra nodes or model
files are installed.

`create-model-set` extracts embedded Hugging Face model hints into a new draft
below `build/model-set-drafts/`. `--name` selects the draft filename; otherwise
the template ID is used. Existing drafts are not overwritten. Complete every
`TODO` before moving the file to `config/model-sets/`.

```bash
bash bin/latentcrate templates list edge
bash bin/latentcrate templates create-model-set video_minimax_h3_i2v edge \
  --name minimax-h3-i2v-review
```

See [Official template tools](templates.md) for the filter rules and review
steps.

## versions

```text
bin/latentcrate versions
```

Prints the pinned, non-comment values of every profile under `versions/`.

```bash
bash bin/latentcrate versions
```
