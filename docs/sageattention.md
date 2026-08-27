# SageAttention

LatentCrate does not include SageAttention in the default image. Opting in
compiles it at the pinned version and CUDA architecture from the selected
profile. It is never downloaded or compiled when the container starts.

```bash
bash bin/latentcrate build current --sage available
bash bin/latentcrate up current --sage available --detach
bash bin/latentcrate smoke-gpu current --sage available
```

The [CLI reference](cli.md) is the main source for these commands and their
flags.

Container caching rebuilds Sage only when a relevant input changes. The image
records the source commit, CUDA architecture list, and wheel digest.

## Workflow-level and global Sage are different

`LATENTCRATE_SAGE=available` adds Sage to the image without forcing every
workflow to use it. Workflow-level patches can then select Sage only where it
is known to work. Repeat `--sage available` with `config` or `smoke-gpu` when
inspecting that running variant, or set `LATENTCRATE_SAGE=available` in `.env`
for a persistent opt-in.

Use `LATENTCRATE_SAGE=global` (or `--sage global` for one command) only after
representative workflows pass with ComfyUI's global `--use-sage-attention`
behavior.

## KJNodes MiniMax H3 Sage patch path

This optional path applies to KJNodes' MiniMax H3 Sage patch. Installing
KJNodes and using its non-Sage nodes do not require SageAttention. ComfyUI's
native Comfy Kitchen attention backend does not require SageAttention either.
All KJNodes Sage patch nodes require a Sage-capable image. The MiniMax H3 patch
currently uses SageAttention 2.2 internal APIs. Follow this path:

1. Install the pinned `latent-nodepack` node set, which includes KJNodes:

   ```bash
   bash bin/latentcrate nodes install latent-nodepack current
   ```

2. Capture its dependencies, build, and start:

   ```bash
   bash bin/latentcrate up current --sage available --detach
   bash bin/latentcrate wait current
   ```

3. In the H3 workflow, use KJNodes'
   `MiniMax H3 Memory Efficient Sage Attention Patch` node.

4. Verify the real GPU path:

   ```bash
   bash bin/latentcrate smoke-gpu current --sage available
   ```

The smoke test imports the KJ-required Sage symbols and runs a small attention
kernel. It also confirms that the running container matches the selected Sage
variant.

The checked-in profiles target compute capability 12.0. For another GPU, change
both `SAGE_CUDA_ARCH_LIST` and `CUSTOM_NODE_CUDA_ARCH_LIST` in a copied profile,
then run the full GPU checklist. Do not select SageAttention 3 only because its
version number is newer; KJNodes currently depends on 2.2 APIs.

## Image targets

The Dockerfile keeps separate final targets so opting in does not add Sage
files to the default image:

- `runtime`: release frontend without Sage;
- `runtime-sage`: release frontend with Sage;
- `runtime-frontend-git`: Git frontend without Sage;
- `runtime-frontend-git-sage`: Git frontend with Sage.
