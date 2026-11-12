# Custom nodes

A custom node is source ComfyUI loads from a `custom_nodes` directory,
plus the Python packages its `requirements.txt` names. LatentCrate keeps
the two apart: the source lives on the host, the packages are built into
the image. This page says how to add a node and make it load.
[Privacy](privacy.md#defaults-and-their-limits) owns what Manager may do.

## Source and dependencies

Node source lives under `COMFY_DATA_DIR/custom_nodes` (the managed
directory, written by Manager or by a node set) or under
`COMFY_LOCAL_NODES_DIR`. `up` and `build` copy each node's Python
requirements into `build/custom-node-requirements`; the image build
validates and rewrites the copy, compiles the packages in the pinned
CUDA development image, and installs them offline, as an unprivileged
user, into an isolated tree that ComfyUI imports through `PYTHONPATH`.
The tree is not a site directory, so `.pth` files in those packages are
ignored. Nothing installs at container start and no compiler enters the
runtime. A package the base environment already provides at the
required version, such as Torch or NumPy, is not duplicated in the
tree. A changed node therefore means a rebuild, and `up` is the command
that does it.

## After a Manager change

Run `bash bin/latentcrate up`. It copies the requirements again,
rebuilds the dependency layer, and recreates the container from the
new image. Packages Manager installs immediately land under
`COMFY_CACHE_DIR/python-user/<runtime id>` and are disposable: the
rebuilt image carries the reviewed copy. When the entrypoint's
`pip check` finds the cached packages in conflict with the image, it
prints the remedy; [troubleshooting](troubleshooting.md) holds the
message and the steps.

## Node sets

```
bin/latentcrate nodes list
bin/latentcrate nodes install <set>
bin/latentcrate nodes sync <set>
bin/latentcrate nodes status <set>
bin/latentcrate nodes check <set>
bin/latentcrate nodes export <custom-nodes-dir>
```

A node set is a manifest under `config/custom-nodes/sets/` that pins
public node repositories to commits; `bash bin/latentcrate nodes list`
prints the shipped sets with their descriptions. `install` clones each
missing node at its commit into a temporary sibling and renames it into
place, so a node directory is either absent or complete; it is
idempotent, an interruption leaves nodes absent, and a second `install`
completes the set. `sync` does the same and also replaces a clean
checkout pinned at another commit. Both refuse while ComfyUI runs and
hold the project lock, so they wait for a build and a build waits for
them. A successful install writes the set name to
`COMFY_DATA_DIR/custom_nodes/.latentcrate-node-set`; `up` reads that
record and refuses to start while a node of the set is missing, naming
`nodes install`. `status` prints one line per node: `ready`, `missing`,
`dirty`, `different-commit`, `different-repository`, or `conflict`.
`check` validates a manifest against
`config/custom-nodes/allowed-git-hosts.txt` without touching the
checkouts. `export` prints a manifest for the clean public checkouts
under any `custom_nodes` directory and, on stderr, the reason it skips
each other directory.

A manifest holds `schema = 1`, one `description` line, and `[[node]]`
entries of `name`, `repository`, and `commit`: a credential-free HTTPS
`owner/repository` URL on an allowed host and a full lowercase commit.
The refusals of `install`, `sync`, and `up` are sections of
[troubleshooting](troubleshooting.md).

## Local nodes

`COMFY_LOCAL_NODES_DIR` holds private or unregistered node source. It
is mounted read-only outside `/data`, so Manager cannot edit or remove
it, and there is no hot reload: after a change, restart with `bash
bin/latentcrate up`. A name must be unique across the managed and local
directories, case ignored. Top-level symbolic links and hidden
directories are refused; a directory whose name ends in `.disabled` is
skipped by ComfyUI and by the dependency copy alike.

## What is captured

- A node's top-level `requirements.txt`, plus the files its relative
  `-r` and `-c` lines include, as long as they stay inside the node
  directory (`latentcrate/runtime.py`).
- A VCS requirement only when `config/custom-nodes/vcs-pins.toml` pins
  it to a commit on a host in `config/custom-nodes/allowed-git-hosts.txt`
  (`services/comfy/rewrite-node-requirements.py`).
- A package replacement from
  `config/python/custom-node-package-replacements.toml`, which keeps one
  OpenCV provider (the same script).
- The constraints in `config/python/custom-node-constraints.txt` and
  `config/python/runtime-constraints.txt`
  (`services/comfy/install-node-deps.sh`, `services/comfy/Dockerfile`).

The reason behind each pin and replacement is a comment in the file
that holds it.

## SageAttention

`LATENTCRATE_SAGE` in `.env`, or `--sage` for one command, selects one
of `off`, `available`, and `global`; `bash bin/latentcrate help` lists
their meaning. One mode drives both the image target and the runtime
flag, so `build`, `up`, and `smoke-gpu` resolve the same
image. The Sage image compiles the SageAttention wheel at build time for
`COMFY_CUDA_ARCH_LIST`, never at start; `global` on an image without
SageAttention is refused by the entrypoint.

The workflow path: install the set that carries KJNodes
(`latent-nodepack`), run with `available`, and add KJNodes' MiniMax H3
Sage attention patch node to the workflow. KJNodes' patch imports
`attn_false`, `get_cuda_arch_versions`, `per_block_int8_triton`,
`per_channel_fp8`, `per_thread_int8_triton`, and `per_warp_int8_cuda`
from `sageattention.core`, functions SageAttention 3 does not provide,
so `SAGEATTENTION_REF` in `pins.env` stays in the 2.2 line until KJNodes
moves; the image build and `smoke-gpu` import the same names. Set
`global` only after representative workflows pass with `available`.
