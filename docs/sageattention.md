# SageAttention and MiniMax H3

LatentCrate builds the SageAttention-capable image by default. SageAttention is
compiled at the pinned version and CUDA architecture from the selected profile.
It is never downloaded or compiled when the container starts.

```bash
bash bin/latentcrate build current
bash bin/latentcrate up current --detach
bash bin/latentcrate smoke-gpu current
```

The [CLI reference](cli.md) is the main source for these commands and their
flags.

Container caching rebuilds Sage only when a relevant input changes. The image
records the source commit, CUDA architecture list, and wheel digest.

## Workflow-level and global Sage are different

Having Sage in the image does not force every workflow to use it. This is the
default because workflow-level patches can select Sage only where it is known to
work.

`COMFY_GLOBAL_SAGE=false` therefore remains the normal setting. Change it to
`true` only after representative workflows pass with ComfyUI's global
`--use-sage-attention` behavior.

To build the smaller image without Sage:

```bash
bash bin/latentcrate up current --no-sage --detach
```

After `up --no-sage`, repeat `--no-sage` with `config` or `smoke-gpu` when
inspecting that running variant. Set `LATENTCRATE_SAGE=false` in `.env` for a
persistent opt-out.

## KJNodes MiniMax H3 path

The MiniMax H3 memory-efficient patch currently uses SageAttention 2.2 internal
APIs. Follow this path:

1. Install the pinned `latent-nodepack` node set, which includes KJNodes:

   ```bash
   bash bin/latentcrate nodes install latent-nodepack current
   ```

2. Capture its dependencies, build, and start:

   ```bash
   bash bin/latentcrate up current --detach
   bash bin/latentcrate wait current
   ```

3. In the H3 workflow, use KJNodes' `Patch Sage Attention KJ` node with the
   selection set to `auto`.

4. Verify the real GPU path:

   ```bash
   bash bin/latentcrate smoke-gpu current
   ```

The smoke test imports the KJ-required Sage symbols and runs a small attention
kernel. It also confirms that the running container matches the selected Sage
variant.

The checked-in profiles target compute capability 12.0. For another GPU, change
both `SAGE_CUDA_ARCH_LIST` and `CUSTOM_NODE_CUDA_ARCH_LIST` in a copied profile,
then run the full GPU checklist. Do not select SageAttention 3 only because its
version number is newer; KJNodes currently depends on 2.2 APIs.

## Image targets

The Dockerfile keeps separate final targets so opting out does not leave Sage
files in the image:

- `runtime`: release frontend without Sage;
- `runtime-sage`: release frontend with Sage;
- `runtime-frontend-git`: Git frontend without Sage;
- `runtime-frontend-git-sage`: Git frontend with Sage.
