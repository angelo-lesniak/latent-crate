# Third-party nodes

ComfyUI calls its extensions "custom nodes." This guide uses "third-party
nodes" for clearer wording, except in literal paths and configuration names.

LatentCrate separates node source from Python dependencies:

- Manager-managed source stays below `COMFY_DATA_DIR/custom_nodes` on the host.
- Private or unregistered source stays below `COMFY_LOCAL_NODES_DIR` and is
  mounted read-only.
- Saved Python dependencies are built into the next container image.

This keeps source and user data easy to edit while making the Python environment
reviewable and repeatable.

## After Manager adds or updates a node

Every `up` captures requirements, builds the image, and recreates the container:

```bash
bash bin/latentcrate up current --detach
```

Use the profile and `--no-sage` choice that you normally run. To capture and
build without starting ComfyUI:

```bash
bash bin/latentcrate build current
```

Use `--use-saved-node-deps` for a one-time opt-out. It keeps node source mounted
but builds from the last saved dependency snapshot.

The snapshot is replaced only after capture succeeds. During automatic refresh,
the previous snapshot remains available until the new image builds. A failed
scan, resolution, or native compile restores the last buildable snapshot.

## Commit-pinned node sets

Node-set files under `config/custom-nodes/sets/` provide repeatable source
groups. Every repository uses public HTTPS and a full commit.

```bash
bash bin/latentcrate nodes list
bash bin/latentcrate nodes install latent-nodepack current
bash bin/latentcrate nodes status latent-nodepack current
bash bin/latentcrate nodes sync latent-nodepack current
```

You can combine first-time initialization and installation:

```bash
bash bin/latentcrate init --node-set latent-nodepack current
```

`install` refuses an existing node at another commit. `sync` replaces only a
clean checkout from the same repository. Both refuse dirty or unrelated
directories. Stop ComfyUI before changing a node set; the wrapper checks this.

Node sets install source only. Capture and rebuild dependencies afterward:

```bash
bash bin/latentcrate up current --detach
```

> **Warning:** commit pinning improves repeatability. It is not a code audit.
> Review the node source before using it with sensitive models, inputs, or
> workflows.

### Export a set from an existing installation

From any LatentCrate checkout that can read the existing ComfyUI installation,
run the exporter against that installation's top-level `custom_nodes`
directory. The LatentCrate and ComfyUI directories can be in different
locations:

```bash
bash scripts/export-node-set.sh /path/to/ComfyUI/custom_nodes \
  > my-nodes.toml \
  2> my-nodes-report.txt
```

The TOML file contains every clean public GitHub checkout at its exact current
commit. Put the reviewed file below `config/custom-nodes/sets/` in that or
another LatentCrate checkout.

The report lists dirty checkouts, private or unsupported origins, symbolic
links, and non-Git/local nodes that cannot be reproduced by a public node set.
It ignores `.disabled`, names ending in `.disabled`, and `__pycache__`. Other
hidden directories are reported because LatentCrate does not load them.
Commit and push changes that belong in a shared repository; copy genuinely
local nodes through `COMFY_LOCAL_NODES_DIR` instead. The exporter does not print
unsupported origin URLs, so embedded credentials are not copied into its
report.

## Local-only and private nodes

Place source directories below the path selected by `COMFY_LOCAL_NODES_DIR`:

```text
local/custom_nodes/
  MyPrivateNode/
    __init__.py
    requirements.txt
```

The whole root is ignored by Git and the container build context. It is mounted
read-only at `/local/custom_nodes` and included in dependency capture.

Node directory names must be unique across managed and local storage, ignoring
case. A duplicate stops capture and startup instead of loading an unclear node.
Top-level symlinks are rejected; put the real checkout below the local-node root.

Private package indexes and private Git credentials are not supported by the
default workflow. They need a separate, engine-compatible secret design.

## Which requirement files are captured?

LatentCrate captures each node's top-level `requirements.txt`. It follows
relative `-r`/`--requirement` and `-c`/`--constraint` files as long as they stay
inside that node directory.

Add another deliberate filename pattern, such as `requirements-cuda.txt`, to:

```text
config/python/custom-node-requirements.include
```

Every configured pattern must match. Development, documentation, and test
requirements are not collected automatically.

The capture helper has no network access. Node trees are read-only, and the
helper receives no engine socket or credentials. Host Python is not required.

## Git requirements and reviewed pins

A Git dependency that points at a branch, tag, or no revision can change over
time. LatentCrate rejects it unless `config/custom-nodes/vcs-pins.toml` contains
an exact reviewed rewrite for that original reference.

The included SAM2 rule changes the saved requirements to commit
`2b90b9f5ceec907a1c18123530e92e794ad901a4`. It does not edit the custom-node
repository. The saved direct reference uses upstream's `sam-2` distribution
name; the import package is still `sam2`. The fixed commit lets pip reuse the
expensive built wheel when the toolchain has not changed.

WAS Node Suite also requests `ltdrdata/img2texture`, `ltdrdata/cstr`, and
`ltdrdata/ffmpy` without revisions. Included rules select reviewed full commits
for all three. The img2texture commit removes an obsolete Pillow upper bound
while keeping the package's existing API. Every included rule matches only a
requirement with no requested branch, tag, or revision.

Already pinned Git commits are accepted only from hosts in
`config/custom-nodes/allowed-git-hosts.txt`. Credential-bearing URLs, direct
HTTP or file packages, node-selected package indexes, trusted-host options, and
local paths are rejected.

## Reviewed package replacements

Some nodes request different OpenCV distributions even though all of them
install the same `cv2` package. Installing several variants lets their files
overwrite each other. The policy in
`config/python/custom-node-package-replacements.toml` replaces
`opencv-python`, `opencv-python-headless`, and
`opencv-contrib-python-headless` with one `opencv-contrib-python` requirement.
That full variant also satisfies MediaPipe's package metadata.

The node's own files are not edited. The saved snapshot contains the effective
requirements and records each change in `package-rewrites.jsonl`. Version
ranges, environment markers, and comments are preserved. An extra is removed
only when its replacement entry explicitly allows that change.

## How the image build handles packages

The resolver starts from the exact ComfyUI Python environment. Packages already
present at the required version, such as Torch or NumPy, are not downloaded into
a second package tree. Only missing dependencies are built in a matching CUDA
development stage.

The final package set is installed as an unprivileged user with networking
disabled, exact wheel hashes, `--no-index`, and `--no-deps`. Compilers and wheel
files do not enter the runtime image. The isolated package tree is loaded through
`PYTHONPATH`, so wheel-provided `.pth` files are not processed.

The runtime also includes the JPEG, JPEG 2000, TIFF, WebP, and X11 libraries
used by the OpenCV, Pillow, frame-interpolation, and video-helper paths in the
included node set. These operating-system packages follow the pinned PyTorch
base image rather than the Python package lock.

This supports pure Python packages, compatible binary wheels, and native
projects that build with the included CUDA toolchain. A project that needs extra
system headers or a special build process still needs a reviewed Dockerfile
change or a pinned compatible wheel.

## Manager's immediate installs

Some Manager operations install a package immediately after cloning a node.
Those packages go to an environment-specific directory under
`COMFY_CACHE_DIR/python-user`. They are disposable and never become durable
`/data` state.

Capture and rebuild after the node works. If startup reports an obsolete or
inconsistent runtime package directory, rebuild the node dependencies and remove
the reported cache directory.

Manager can manage nodes and models. It cannot update the root-owned ComfyUI
checkout inside the image. Update ComfyUI and its frontend through a version
profile, then rebuild.

## Project-specific constraints

Use `config/python/custom-node-constraints.txt` for reviewed version decisions
and `config/python/custom-node-package-replacements.toml` when several package
names provide the same import tree. Do not add a rule simply to make an error
disappear. Record why it is compatible with ComfyUI, Torch, and the affected
nodes.

## Manual snapshot commands

Capture the current managed and local-node requirements without building the
runtime image:

```bash
bash bin/latentcrate node-deps snapshot current
```

This writes the snapshot below `build/custom-node-requirements`. The saved
requirements are installed the next time `up` or `build` runs. This is useful
for reviewing what a rebuild would install before starting it.

Clear all saved node requirements with:

```bash
bash bin/latentcrate node-deps clear
```

This is useful for a clean diagnostic build. The next refresh creates a new
snapshot from the current managed and local nodes.
