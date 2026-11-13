# Frontends

The ComfyUI frontend is a separate web application that ComfyUI serves
as a built `dist/` tree through `--front-end-root`. LatentCrate pins one
and lets you test another against the same pinned backend; Node runs
only in a build stage or a build container, never in the runtime image.

## Modes

`COMFY_FRONTEND_MODE` in `.env` selects `release`, `git`, `dist`, or
`source`; a frontend flag of `up` or `build` selects a mode for one
command and wins over the setting. `release`, the default, is the Git
build of `COMFYUI_FRONTEND_COMMIT` in `pins.env`, the commit of the tag
pinned as `COMFYUI_FRONTEND_REF`; `pins check` reports a tag that moved.

| Mode | Input | Where its code runs |
| --- | --- | --- |
| `release` | The pinned upstream tag and commit | Build stage of the image |
| `git` | A public HTTPS repository and a reference | Build stage of the image |
| `source` | A checkout on the host | Two runs of the pinned Node image |
| `dist` | A prebuilt `dist/` tree on the host | Nowhere; served as is |

In every mode the resulting JavaScript runs in the browser with access
to the ComfyUI API, so build only frontends you trust.

```
bin/latentcrate up --frontend-git <https-url> <ref> [--sage mode] [--detach] [--timeout seconds] [--model-set name]...
bin/latentcrate build --frontend-git <https-url> <ref> [--sage mode]
```

Both build the image with the frontend of that commit; `up` also
starts it. `--frontend-dist` and `--frontend-source` belong to `up`
only.

## Git mode

The URL is HTTPS without credentials. The reference is a branch, tag,
commit, or pull-request reference. `build` and `up` resolve it on the
host with hardened Git to a commit, and that commit is the build
argument, so a moved branch rebuilds the image. A name that is both a
branch and a tag is refused: write `refs/heads/<name>` or
`refs/tags/<name>`. The image records the URL, the requested reference,
and the commit in its labels and under `/usr/local/share/latentcrate`.

## Source mode

The checkout needs `package.json` and `pnpm-lock.yaml`. `up` copies it
without `.git`, `node_modules`, `.pnpm-store`, and the top-level `dist`,
then builds it in two runs of the pinned Node image: the first installs
the lockfile's packages with `--ignore-scripts` and network access, the
second runs with `--network none` and builds. The pinned pnpm is used
even when `package.json` names another through `packageManager`. The
result lands under `COMFY_CACHE_DIR/frontend-builds/<id>/dist` and is
served by the dist overlay, so the image is not rebuilt. The built
`dist/` is used instead of the upstream development server because
custom-node JavaScript loads differently from the built files. pnpm
prints `[WARN] Unsupported engine` when `package.json` wants another
Node major than the pinned image; the build continues. `build`
refuses this mode and dist mode: `build supports the pinned and Git
frontends; use up for --frontend-source or --frontend-dist`.

## Dist mode

A prebuilt `dist/` tree from the host is bound read-only at the
entrypoint's frontend root and served as is; nothing is rebuilt. The
tree must contain `index.html` and no symbolic links. In dist and
source mode a Sage image (`--sage`) is built with the pinned release
frontend, because the Sage target builds on the runtime image; the
overlay still serves your tree. `smoke-gpu` reads the
settings and `--sage` only, so testing a dist stack needs
`COMFY_FRONTEND_MODE=dist` and `COMFY_FRONTEND_DIST_DIR` in `.env` or
the shell. A source stack needs `COMFY_FRONTEND_MODE=source` and
`COMFY_FRONTEND_SOURCE_DIR` the same way; `smoke-gpu` builds nothing.
