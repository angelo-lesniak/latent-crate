# Frontend modes

LatentCrate versions the ComfyUI backend and frontend independently.
All modes ultimately supply a built `dist/` directory to ComfyUI through
`--front-end-root`; Node.js is never included in the runtime image.

| You want to… | Use |
| --- | --- |
| Run the frontend pinned by the profile | Release mode, the default |
| Test a public fork, branch, or pull request | `--frontend-git` |
| Test uncommitted source without host Node.js | `--frontend-source` |
| Serve frontend files built by another trusted system | `--frontend-dist` |

All four modes use the Sage-capable runtime by default. Add `--sage off` only
when you want the smaller runtime variant. The
[CLI reference](cli.md#frontend-flags) is the main source for these flags; the
table below maps each flag to its mode.

| Flag | `COMFY_FRONTEND_MODE` value |
| --- | --- |
| `--frontend-release` | `release` |
| `--frontend-git` | `git` |
| `--frontend-source` | `source` (local source mode) |
| `--frontend-dist` | `dist` (prebuilt dist mode) |

## Pinned release mode

Release mode is the default. `COMFYUI_FRONTEND_REF` in the selected
`versions/*.env` file is resolved while the image is built and copied to
`/opt/latentcrate-frontend`. It must be an exact `OWNER/REPO@vVERSION`
reference. The builder downloads that tag's `dist.zip` asset directly over
HTTPS, without enumerating releases through the anonymous GitHub API, and
rejects unsafe or unexpectedly large archives.

The profile's optional `COMFY_FRONTEND_DIST_SHA256` pins the archive bytes as
well as its release tag. After changing `COMFYUI_FRONTEND_REF`, refresh that
digest with `bash bin/latentcrate frontend pin-release <profile>`. The command
downloads and validates the archive in a short-lived helper container with
tmpfs storage and no host bind mounts, then updates the profile on the host.

```bash
bash bin/latentcrate up current --detach
bash bin/latentcrate wait current
```

The requested release and a digest of the installed assets are stored below
`/usr/local/share/latentcrate` in the image.

## Trusted Git mode

Git mode builds a public fork, branch, pull-request reference, tag, or commit in
an isolated Node stage and copies only `dist/` into the runtime image. Use only
trusted source: package installation and frontend build scripts execute code
inside the builder, and the resulting JavaScript runs in the browser with
access to the ComfyUI API.

```bash
bash bin/latentcrate up edge \
  --frontend-git https://github.com/your-name/ComfyUI_frontend.git your-branch \
  --detach
```

For an upstream GitHub pull request:

```bash
bash bin/latentcrate up edge \
  --frontend-git https://github.com/Comfy-Org/ComfyUI_frontend.git \
  refs/pull/13758/head \
  --detach
```

The wrapper resolves branches, tags, and pull-request references that can change
to a full commit with `git ls-remote` before building. The commit becomes a build
argument, so a newly pushed commit invalidates the frontend build cache without
requiring `--no-cache`.

Repeat the Git frontend option when selecting that image for `smoke-gpu`
verification:

```bash
bash bin/latentcrate smoke-gpu edge \
  --frontend-git https://github.com/your-name/ComfyUI_frontend.git your-branch
```

For a repeatable shared profile, put these values in a new checked-in
`versions/<name>.env` file:

```dotenv
COMFY_FRONTEND_MODE=git
FRONTEND_GIT_URL=https://github.com/your-name/ComfyUI_frontend.git
FRONTEND_GIT_REF=<full-40-character-commit>
```

Copy all other required pinned versions from an existing profile. Git URLs
containing credentials are rejected. Private repositories and build credentials
are not supported by the default setup.

Git images record the requested reference, resolved commit, repository URL, and
asset content digest below `/usr/local/share/latentcrate`.

## Local source mode

Source mode is the recommended path for uncommitted frontend development. It
needs no host Node.js, npm, or pnpm installation:

```bash
bash bin/latentcrate up edge \
  --frontend-source /path/to/ComfyUI_frontend \
  --detach
```

LatentCrate builds a small tool image from the selected profile's
`FRONTEND_NODE_IMAGE` and `FRONTEND_PNPM_VERSION`. The source checkout is mounted
read-only into two temporary containers:

1. `frontend-fetch` runs a frozen pnpm install with registry access and
   lifecycle scripts disabled. This fills the dedicated pnpm store below
   `COMFY_CACHE_DIR`, including the metadata needed by the offline phase.
2. `frontend-build` copies the source into a temporary work directory below the
   configured host cache, trusts the frozen lockfile already checked by the
   first phase, installs from the store, and creates the built frontend with
   networking disabled. Each phase starts with a clean work directory and
   removes its temporary files after success.

Both helpers run as `HOST_UID:HOST_GID` with a read-only root filesystem, all
capabilities dropped, and `no-new-privileges`. They receive neither the engine
socket nor unrelated host directories. Only its work directory below the
cache, the pnpm store, and—for the offline phase—the selected frontend output
directory are writable. The finished `dist/` is checked for symbolic links and
atomically published below
`COMFY_CACHE_DIR/frontend-builds/<profile>/` before it is mounted read-only into
ComfyUI.

This isolation limits what package installation and build scripts can reach; it
does not make untrusted frontend code safe. The fetch phase has network access,
and the resulting JavaScript executes in the browser with access to the ComfyUI
API. Do not place credentials in the source checkout or package-manager
configuration. Private registries and host credential forwarding are not
supported by the default setup.

Rerun the same `up --frontend-source` command after changing the source. The
frontend tool image and pnpm store are reused, the frontend files are rebuilt,
and the ComfyUI container is recreated so its read-only bind mount refers to the
new, completely written output. The CUDA runtime image is not rebuilt unless its
own inputs changed.

Use the same source path when running `smoke-gpu`. It selects the existing
managed build instead of building potentially different assets during
verification:

```bash
bash bin/latentcrate smoke-gpu edge \
  --frontend-source /path/to/ComfyUI_frontend
```

The smoke report verifies the digest of the exact `dist/` tree being served.
Source and output digests plus the effective Node and pnpm versions are retained
beside the generated assets under `build-info/`.

The shipped profiles use Node 26 and pnpm 11 in builder-only containers. To keep
the second phase fully offline, LatentCrate uses the pnpm version from the
selected version profile instead of downloading another pnpm release named in
the checkout's `packageManager` field, including for nested pnpm commands run by
package lifecycle scripts. Compatible pnpm 11 patch versions work with the same
lockfile format. If a fork needs another pnpm major or a different Node range,
copy a version profile and update the two frontend toolchain pins.

The built frontend is used because the upstream development server does not load
custom-node JavaScript extensions in the same way as the final `dist/` files.

## Prebuilt dist mode

`--frontend-dist` is available for CI or another trusted build system that
already produced a complete `dist/`:

```bash
bash bin/latentcrate up edge \
  --frontend-dist /path/to/ComfyUI_frontend/dist \
  --detach

bash bin/latentcrate smoke-gpu edge \
  --frontend-dist /path/to/ComfyUI_frontend/dist
```

LatentCrate does not run package tools in this mode. The supplied directory is
mounted read-only at `/opt/latentcrate-frontend-dist`, and `smoke-gpu` records
its content digest. The producer is responsible for matching the frontend's
required Node and package-manager versions.

Return to the pinned frontend with:

```bash
bash bin/latentcrate up edge --frontend-release --detach
```

## Pinned build-tool versions

The Node base image and pnpm version live in the version profile beside the
frontend release. The same profile also pins the small Python image used by the
custom-node snapshot helper. A fork may change its Node or package-manager
requirements; update those pins together and validate both the frozen install
and final frontend build before sharing the profile.
