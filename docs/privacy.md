# Privacy

ComfyUI, Manager, and every custom node run in one container on your
machine; the package runs the rest on the host. Nothing below makes
third-party code trustworthy.

## What runs where

| Component | Runs |
| --- | --- |
| ComfyUI, Manager, custom nodes | in the `comfy` container, as the calling user |
| model downloads, checks, and drafts (`models ...`) | in the package on the host |
| node-set installs (`nodes install`, `nodes sync`) | as `git` on the host, against allowlisted public HTTPS hosts |
| source-mode frontend builds | in the pinned Node image through `engine run`: one phase with network for the package store, one with `--network none` for the build |
| `templates list`, `templates create-model-set` | in a one-off `engine run` of the built image: no network, read-only root, no `/models`, `/data`, or `/cache` mount; `create-model-set` mounts `build/model-set-drafts` only |
| `pins check` | in the package on the host, reading public registries over HTTPS |
| the image build | in the engine's builder |

`HF_TOKEN` and `CIVITAI_TOKEN` are read from the shell or `.env` by
every command that loads settings, used by the model commands only, and
removed from the environment of every subprocess; neither reaches a
container, Compose file, or image. The package sends each as an
`Authorization` header to its own hosts only, `huggingface.co`, or
`civitai.com` and `civitai.red`, and drops it on a redirect to any other
host. `models fetch` and `draft` contact `civitai.com` only for a
Civitai file, `check --remote` also for a `civitai.com` license link;
Civitai redirects each download to a CDN host, which
receives the request without the token. See
[Models](models.md#fetch-a-set) for the token setup and the licenses the
downloader prints, and [Civitai sources](models.md#civitai-sources) for
the risks of that source.

## Defaults and their limits

The UI port binds to `127.0.0.1`; `[::1]` is the other address that needs
no acknowledgement. Any other `COMFY_BIND_ADDRESS` is refused unless
`COMFY_ALLOW_REMOTE=true` acknowledges that authentication and TLS exist
in front of ComfyUI, and `localhost` is not a binding Compose accepts.

With Manager enabled, the first start of a fresh data directory writes the
Manager policy file to `/data/user/__manager/config.ini`; an existing
file there, or at `/data/user/default/ComfyUI-Manager/config.ini`, is
left alone. The policy disables sharing, direct Git URL and arbitrary pip
installs, agent model downloads, and file logging, keeps TLS
verification, and leaves the public channel on so the catalogue and
registry installs work; the shipped file is
[`config/comfy/manager-config.ini`](../config/comfy/manager-config.ini).

The `comfy` service runs with a read-only root filesystem, every Linux
capability dropped, `no-new-privileges`, a process limit, and a bounded
in-memory `/tmp`; only the four mounts of
[Storage layout](configuration.md#storage-layout) and a dist-mode
frontend are reachable. Third-party nodes are not sandboxed: they run in
the ComfyUI process with its access to every mount and to the network.

## Strict mode

`COMFY_DISABLE_API_NODES=true` passes `--disable-api-nodes` to ComfyUI,
which removes the external API nodes and adds a Content Security Policy
that blocks frontend internet access; it also blocks Manager's Extensions
UI. Set `COMFY_ENABLE_MANAGER=false` with it when Manager is not needed.
`COMFY_DISABLE_METADATA=true` passes `--disable-metadata`, so generated
files carry no prompt or workflow. Neither setting limits what Python
code inside the container sends out.

## No internal network

A Compose `internal` network would cut the container's outbound traffic,
but it also drops the published port, so the UI would be unreachable from
the host. LatentCrate therefore offers no offline network mode; outbound
containment is a host firewall rule on the container's traffic, or a
proxy, maintained outside this project.

## What is not covered

The defaults do not protect against an untrusted node, which runs with
the container's access; against exposing the UI beyond loopback, where
authentication and TLS are the acknowledging user's responsibility;
against the engine itself, whose security model is the host's; or against
the outbound traffic of nodes and of the frontend outside strict mode.
Direct Compose calls bypass the package's checks: the bind-address
acknowledgement, the `.env` rules, the project lock, and the stack
record. [SECURITY.md](../SECURITY.md) says how to report a vulnerability.
