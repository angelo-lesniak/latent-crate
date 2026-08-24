# Upgrade workflow

## Updating LatentCrate itself

Update the project files, then let the wrapper prepare and rebuild:

```bash
git pull
bash bin/latentcrate init
bash bin/latentcrate doctor current
bash bin/latentcrate up current --detach
```

`init` is safe to re-run: it keeps an existing `.env` unchanged and only
creates missing host directories. Compare your `.env` with `.env.example`
after a pull to see new settings. `up` rebuilds the image only when its inputs
changed, so an update without version or dependency changes reuses the cached
build.

## Updating pinned components

Upgrade and validate each independent component or compatibility group
separately.

1. Copy the currently validated version profile.
2. For a component listed in the [version updater](cli.md#versions), run
   `bash bin/latentcrate versions update <component> <profile>`. It resolves the
   latest eligible stable version, prints available intervening release links,
   and atomically updates the profile. A frontend resolution always downloads
   and validates the selected `dist.zip`, updates its SHA-256 when needed, and
   removes the temporary archive. For a setting outside the updater's table,
   edit the copied profile directly and use its entry in
   [configuration](configuration.md#version-profiles) to preserve compatibility.
3. Review the profile diff and the upstream release information. If you change
   `COMFYUI_FRONTEND_REF` manually instead, run
   `bash bin/latentcrate frontend pin-release <profile>` to refresh only the
   digest.
4. Build from a clean third-party node dependency snapshot.
5. Run static checks.
6. Run `doctor` and `smoke-gpu` on a supported Arch/NVIDIA host.
7. Start ComfyUI and exercise representative workflows.
8. Recreate the container and confirm user state and outputs remain present.
9. Record the resolved image and frontend build information with the test result.

Do not update independent groups such as ComfyUI, its frontend, the CUDA
toolchain, SageAttention, and FFmpeg all at the same time; the combined change
cannot be reviewed or debugged. Treat PyTorch, TorchCodec, its package index,
and CUDA NPP as the compatibility group defined in
[configuration](configuration.md#torchcodec-and-cuda-npp). The updater can
refresh the PyTorch image pair and TorchCodec version, but the index and NPP
selection remain manual. A newer frontend paired with a pinned
backend is an intentional LatentCrate use case, but both references should
remain exact.

`bash bin/latentcrate versions update all <profile>` is available for an
intentional coordinated refresh. The command writes resolved updates after all
sources succeed; review the resulting profile diff. It does not replace the
one-group-at-a-time validation workflow above. Review the updater's documented
[manual compatibility settings](cli.md#versions) when a toolchain family
changes.

Friendly Git tags and OCI tags can be moved upstream, so the checked-in profiles
are stable version selections rather than byte-for-byte supply-chain locks.
LatentCrate records resolved source commits in each image. For a published release,
promote reviewed Git references to full commit hashes and pin the PyTorch and
Node/Python tool base images by registry digest while retaining readable version
comments.

When testing a frontend fork, replace a branch or pull-request reference that
can change with its resolved full commit. Record the frontend content digest as
well as the commit; release frontends are packaged files and might not have a
Git checkout inside the image.

If `smoke-gpu` reports stale Triton or Torch extensions after a Python/CUDA
base upgrade, clear or move the compiled cache directories `triton/` and
`torch-extensions/` under `COMFY_CACHE_DIR` (see the
[storage layout](storage.md)).

BuildKit cache mounts retain apt, pip, frontend, third-party node wheel, and Sage
build files without copying those caches into the final image. GitHub image builds
also export their multi-stage cache through the GitHub Actions cache backend.
Cache hits improve build time but do not replace source/build records or
validation tests.
