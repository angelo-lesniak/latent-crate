# Storage layout

LatentCrate separates durable state, models, and disposable caches.

```text
/data
  custom_nodes/
  home/
  input/
  output/
  user/
/models
  checkpoints/
  diffusion_models/
  loras/
  text_encoders/
  vae/
  ...
/cache
  frontend-builds/
  frontend-pnpm/
  huggingface/
  pip/
  temp/
  torch-extensions/
  triton/
  xdg/
/local
  custom_nodes/  (read-only in the runtime)
```

The matching host paths are set in `.env`. They may point at existing storage;
there is no requirement that models and ComfyUI state share a parent directory.
Containerized local frontend source builds keep their generated assets, pnpm
store, and isolated build workspace below `/cache` on the host side. Those
helper directories are not mounted as executable tooling into the ComfyUI
runtime. Only the selected generated `dist/` is added through its dedicated
read-only bind mount.
Local-only nodes are kept outside `/data` so Manager cannot modify them;
their Python requirements still participate in normal dependency capture.

ComfyUI receives `--base-directory /data`, `--models-directory /models`, and
`--temp-directory /cache`. ComfyUI appends its own `temp` component, producing
the documented `/cache/temp` location. The user directory and SQLite database
are explicitly set to `/data/user` and `/data/user/comfyui.db`; they do not rely
on ComfyUI deriving database storage from the base directory.
`config/comfy/extra_model_paths.yaml` preserves the expanded model directory layout while
using `/models` as its portable base.

## Model symlinks and multiple storage devices

A model symlink is resolved inside the container. A link whose target stays
below `COMFY_MODELS_DIR` works without another mount. A link that points outside
that directory needs an explicit bind mount at the path stored in the link.

For example, if `/models/vae` points to `/mnt/llm/vae`, add this mount to the
`comfy` service in `compose.yaml` as a local machine change:

```yaml
services:
  comfy:
    volumes:
      - type: bind
        source: /mnt/llm/vae
        target: /mnt/llm/vae
        read_only: true
```

The host `source` may use another path, but the container `target` must match
the symlink target exactly. Keep model mounts read-only when ComfyUI does not
need to download into them. The container user must still have permission to
read every parent directory and file.

LatentCrate does not discover or mount symlink targets automatically. A model
tree must not be able to expose arbitrary host paths to the container. Keep
machine-specific mount changes local and review them before each project
update. Use Linux symlinks under WSL2; Windows junction behavior is not part
of the supported storage contract.

The model-set downloader applies the same rule and also needs write access to
its destination. For an external symlink target, add the bind to `model-set`
and a read-only copy to `model-set-status`, then list the container target in
`MODEL_SET_EXTRA_WRITE_ROOTS`. See [Model sets](model-sets.md#existing-files-failures-and-storage).

`/data` and `/cache` must be writable by `HOST_UID:HOST_GID`. `/models` must be
readable and may be mounted read-only through a local Compose change when model
downloads are not needed.
`COMFY_LOCAL_NODES_DIR` must be readable and is mounted read-only. Do not use the
same directory for it and `/data/custom_nodes`; equal or case-insensitively
duplicate node names are rejected.

Docker can add `HOST_MODEL_GID` for model trees shared through a supplementary
group. Rootless Podman uses `keep-groups` with `crun` to preserve the invoking
user's supplementary groups. `doctor` checks the permissions of the storage
root directories, but the Arch validation guide also verifies access from
inside the real container.

LatentCrate does not ship an SELinux-specific Compose overlay. Users on an
SELinux-enforcing host may need to supply appropriate bind-mount relabeling or
security options for their local policy.

## Removing LatentCrate and reclaiming disk space

### What `down` does and does not remove

`bash bin/latentcrate down <profile>` stops and removes the ComfyUI container
and its Compose network. It does not remove:

- the built images;
- the engine's BuildKit build cache;
- any host directory (`COMFY_DATA_DIR`, `COMFY_MODELS_DIR`, `COMFY_CACHE_DIR`,
  `COMFY_LOCAL_NODES_DIR`);
- the saved dependency snapshot under `build/` and GPU reports under `reports/`
  in the checkout.

### Which host directories hold irreplaceable data

| Directory | Contents | Safe to delete? |
| --- | --- | --- |
| `COMFY_DATA_DIR` | Workflows, settings, inputs, outputs, Manager-installed nodes | No: irreplaceable user data |
| `COMFY_MODELS_DIR` | Model library | Only if every model can be downloaded again |
| `COMFY_LOCAL_NODES_DIR` | Private or local-only node source | No: may be the only copy |
| `COMFY_CACHE_DIR` | Downloads and compiled caches | Yes: recreated automatically |

> **Warning:** back up `COMFY_DATA_DIR` and any local node source before a
> complete removal. Nothing in LatentCrate can restore them.

### Removing images

LatentCrate builds local images named `latentcrate/comfy` (the runtime, one tag
per profile and variant, for example `current` or `current-sage`) and
`latentcrate/tools` (small helpers with tags such as `current-node-deps`,
`current-node-set`, `current-model-set`, `current-frontend-release`, and
`current-frontend`). The full tag scheme is described in
[image names and tags](configuration.md#advanced-image-names-and-tags).
List and remove the images:

```bash
docker image ls 'latentcrate/*'
docker image rm latentcrate/comfy:current
docker image rm latentcrate/tools:current-node-deps
docker image prune
```

With Podman, use the same commands with `podman` instead of `docker`. If you
set `LATENTCRATE_IMAGE` or `LATENTCRATE_TOOLS_IMAGE` in `.env`, use those names
instead.

### Pruning the build cache

The build cache is separate from the images and can be large:

```bash
docker builder prune
```

If your Podman version provides the `builder` subcommand (check
`podman builder --help`), use the same command as `podman builder prune`.
`podman system prune` also removes the build cache, but it removes all other
unused containers, images, and networks as well, so read its prompt carefully.
After a prune, the next build is a slow full rebuild.

### Complete removal checklist

1. Run `bash bin/latentcrate down <profile>` for every profile you started.
2. Remove the `latentcrate/comfy` and `latentcrate/tools` images.
3. Prune the build cache.
4. Back up, then delete, the host storage directories if you no longer need
   them. Their locations are listed in `.env`.
5. Delete the repository checkout. It contains `.env`, the dependency snapshot
   under `build/`, and reports under `reports/`.
