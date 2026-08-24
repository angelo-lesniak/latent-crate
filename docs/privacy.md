# Privacy and containment

LatentCrate is local-first, but it runs third-party Python and JavaScript. Its
privacy defaults reduce accidental exposure and persistence; they cannot make
an unreviewed third-party node trustworthy.

For a normal local computer, keep the defaults. Common deliberate changes
are:

| Goal | Setting or action |
| --- | --- |
| Do not use Manager | `COMFY_ENABLE_MANAGER=false` |
| Remove prompts and workflows from generated-file metadata | `COMFY_DISABLE_METADATA=true` |
| Block API nodes and frontend internet | `COMFY_DISABLE_API_NODES=true` |
| Use a strict browser-offline setup | Also set `COMFY_ENABLE_MANAGER=false` |
| Reach another computer safely | Keep the local bind and use an SSH tunnel |

Review the limits below before processing sensitive material or enabling code
from a new third-party node.

## Defaults

- The host port binds to `127.0.0.1`. The wrapper refuses any non-loopback
  `COMFY_BIND_ADDRESS` unless `COMFY_ALLOW_REMOTE=true` explicitly acknowledges
  that authentication and TLS are external responsibilities.
- `COMFY_DISABLE_API_NODES=false` keeps ComfyUI's external API nodes available
  and lets Manager's Extensions UI reach `https://api.comfy.org`. The upstream
  [`--disable-api-nodes`](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/cli_args.py)
  flag combines API-node removal with a browser-offline Content Security Policy,
  so these behaviors cannot be selected separately. The link shows upstream
  `master`; LatentCrate builds the ComfyUI version pinned in the selected
  `versions/*.env` profile.
- Manager hides sharing, keeps TLS verification enabled, rejects direct Git URL
  and arbitrary pip installs, and does not persist its file log. It remains in
  `network_mode=public` so its catalogue and registered-node/model installation
  work. Set `network_mode=offline` in the persisted Manager config, or disable
  Manager with `COMFY_ENABLE_MANAGER=false`, when those functions are not needed.
- The service uses a read-only root filesystem, a constrained `/tmp`, no Linux
  capabilities, `no-new-privileges`, and a PID limit. Only the declared data,
  model, cache, local-node, and frontend-dist mounts remain accessible according
  to their mount options. Local-node source and mounted frontend dist assets are
  read-only.

Manager configuration is initialized only for a fresh data directory. Existing
`/data/user/__manager/config.ini` and the alternate Manager configuration path
are preserved.
To adopt the privacy defaults in an existing installation, review and add
these shipped defaults, copied from `config/comfy/manager-config.ini`:

```ini
[default]
security_level = normal
downgrade_blacklist = torch, torchvision, torchaudio, triton
share_option = none
network_mode = public
http_channel_enabled = false
bypass_ssl = false
allow_git_url_install = false
allow_pip_install = false
model_download_by_agent = false
default_cache_as_channel_url = false
file_logging = false
```

## Strict browser-offline mode

Set both values when API nodes, Manager, and frontend internet access are not
needed:

```dotenv
COMFY_DISABLE_API_NODES=true
COMFY_ENABLE_MANAGER=false
```

The API-node setting adds ComfyUI's strict Content Security Policy. Enabling it
while keeping Manager enabled is expected to block browser requests used by the
Extensions UI; this check is not yet validated (see
[validation status](validation-status.md)). This mode does not block runtime
network access for third-party Python code; use host firewall rules when
outbound containment is required.

## Output metadata

Set `COMFY_DISABLE_METADATA=true` to pass `--disable-metadata`. This prevents
ComfyUI from embedding prompts and workflows in generated files, which is useful
before publishing outputs. This setting is disabled by default because embedded
workflows are also valuable for reproducing results.

## What the protections do not cover

> **Warning:** third-party nodes execute in the ComfyUI process. They can read
> mounted inputs, outputs, workflows, cache content, and models, and the
> normal runtime network allows outbound connections. The API-node flag and
> container restrictions do not prevent deliberate data theft by such code.
> Review and pin nodes, capture their dependencies, avoid putting unrelated
> secrets under mounted paths, and use a firewall or another outbound-network
> rule when processing sensitive data.

Node-set installation and dependency capture do not receive engine credentials.
Node sets allow only configured public HTTPS hosts and exact commits. This
improves repeatability, but commit pinning is not a code audit: installed node
source still executes with the runtime's access to mounted data and network.

Model-set downloads use a separate helper with access only to the model-set
manifests, model library, Hugging Face sub-cache, temporary directory, and
normal network. The status helper receives only the manifests and read-only model
library, with networking disabled. When `HF_TOKEN` is set, the wrapper pipes it
through the fetch helper's standard input; it is not added to Compose
configuration or a container environment. The downloaded files and Hugging
Face cache are still untrusted input. Model licenses and repository access
rules remain the user's responsibility.

The frontend release pin helper has normal network access but no host bind
mounts. It downloads the selected `dist.zip` into container tmpfs, validates
the archive, and prints its digest. The wrapper validates that output before it
updates the selected version profile on the host. This helper and the version
update helper use a dedicated Compose network that is not shared with ComfyUI.

The version update helper likewise has normal network access and no host bind
mounts or engine credentials. It receives only the checked-in version values
needed for resolution and queries public GitHub, GitLab, npm, PyTorch package
index, and Docker Hub endpoints. For a frontend update it also downloads the
release archive into tmpfs. The helper cannot edit the repository: it prints a
labeled proposal, and the host wrapper allowlists the keys and value formats
before atomically updating the selected profile. Version profiles are not a
place for secrets; the supplied values appear in the short-lived container's
command arguments.

Dependency isolation works like this:

- **What enters the final image:** the isolated package tree built from the
  saved node requirements, plus aggregate build IDs. The tree necessarily
  exposes installed import and distribution names.
- **What is deliberately excluded:** the wheels themselves, NVCC and the CUDA
  development toolchain, host node paths, and the package-lock records used to
  construct the tree. Installation happens in an unprivileged intermediate
  stage. General build helpers such as GCC, CMake, and Ninja remain available
  for Triton/PyTorch JIT work; third-party nodes can invoke them.
- **What this prevents:** build-time packages replacing root-owned
  entrypoints, and private node directory names appearing in image metadata.
- **What it does not prevent:** anything the node does after ComfyUI imports
  it at runtime. The node is not sandboxed.

An engine-level `internal` network is not offered as a portable offline mode
because it can block the published localhost port together with egress.
Docker and Podman need a separately tested proxy or host-firewall design for an
offline UI that remains reachable.

The supported interface is `bash bin/latentcrate ...` (see the
[CLI reference](cli.md)). Manually bypassing it with direct Compose calls also
bypasses the remote-bind acknowledgement and
requires internal build variables and engine overlays. Keep
`COMFY_BIND_ADDRESS=127.0.0.1`, or put an authenticated TLS reverse proxy in
front of ComfyUI before opting into remote access.
