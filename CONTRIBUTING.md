# Contributing

This page is for developers changing LatentCrate. Preserve the shared
Compose core for Docker and Podman, host-mounted data, the single pin
file `pins.env`, and the [privacy defaults](docs/privacy.md).

## Tools

Install Python 3.11 or newer, Git, ShellCheck, and ripgrep. Install the Python
test dependencies in a virtual environment and activate it before
running the tests, because `tests/static.sh` runs the `python3` on
`PATH`; the pinned `podman-compose` from `requirements-dev.txt` renders
the Compose files without a container engine:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
```

## Layout

Keep behavior in the `latentcrate/` package, which uses only the Python
standard library and targets Python 3.11 (`ruff.toml`, the floor
`bin/latentcrate` enforces); `bin/latentcrate` only launches it. The
image's shell and Python live under `services/comfy/` and in
`scripts/manage-templates.py`. `config/` holds the manifests
(`model-sets/`, `custom-nodes/sets/`, `workflow-sets/`) and the policy
files the image copies in. `Settings.storage` in `latentcrate/env.py`
is the shared list of host paths; `init` and `doctor` iterate it
instead of naming directories. Each module's docstring says what it does.

[Source and dependencies](docs/nodes.md#source-and-dependencies) says
how node requirements reach the image; `services/comfy/install-node-deps.sh`
holds the rules. `latentcrate/variants.py` derives the final target and
the image tag ([Removal](docs/configuration.md#removal)) from the
frontend and Sage modes; the Dockerfile header is the stage map.

## Tests

```bash
bash tests/static.sh
```

Run the static gate after every change. It needs no container engine,
no GPU, and no model, and [`tests/static.sh`](tests/static.sh) lists its
checks. A missing optional tool prints a `SKIPPED:` line and the gate
still passes; `LATENTCRATE_STATIC_STRICT=1`, as in CI, makes any skip a
failure. CI (`.github/workflows/static.yml`) runs the strict gate on
Python 3.11 from a clean checkout, so a check must not depend on a local
build artifact, and lints the Dockerfile with hadolint.

Image builds and GPU checks run on the GPU host;
[Procedures](docs/validation.md#procedures) holds the baseline, the
feature checks, and how to record a run.

## Documentation changes

Write and review documentation with the `documentation-guidelines` skill
from [SkillCrate](https://github.com/angelo-lesniak/skill-crate): copy
`skills/documentation-guidelines` from that repository into your agent's
skills directory. This section is the checklist the skill asks for.

Each fact has one owner page; update it instead of restating the fact
elsewhere:

- `README.md#start-here`: the install and the quick start;
  `README.md#scope-and-support`: the supported platforms (status itself
  on validation).
- `docs/configuration.md`: settings pointer and precedence, storage
  layout and the symlink bind rule, GPU and engine support (CDI, driver
  floor, arch list, `keep-groups`/`crun`, `HOST_MODEL_GID`), when a
  change takes effect, pins and upgrading, updating the checkout,
  removal; `.env.example` is the settings reference, `pins.env` the pin
  reference.
- `docs/models.md`: model sets, Hugging Face and Civitai access,
  licenses, stamps, manifest keys, the templates tools and drafts.
- `docs/nodes.md`: Manager installs, node sets, local nodes, captured
  dependencies and the policy files, SageAttention.
- `docs/workflows.md`: workflow sets, provenance, manifest keys,
  private sets, `workflows check`, `workflows adapt`, and equivalent
  model files.
- `docs/frontends.md`: the frontend modes.
- `docs/privacy.md`: what runs where, where each token goes, the network
  and Manager defaults and their limits, strict mode.
- `docs/troubleshooting.md`: observed failures, one section each.
- `docs/validation.md#status`: the only statement of what is validated;
  procedures; WSL2 differences; known gaps.
- `CONTRIBUTING.md#settled-decisions`: design choices with reason and
  reconsideration condition.

There is no glossary, word list, or changelog before the first tagged
release; Git history records changes.

`tests/test_project_contracts.py` checks that links and anchors
resolve, that the README's "Choose what to do next" table links every
docs page, and that every usage line of `bin/latentcrate help` sits
verbatim on exactly one page. Check the facts in the prose by hand.

## Add a pin

A pin in `pins.env` is `<NAME>_TAG` with `<NAME>_DIGEST` beside it (the
tag names the image, the manifest digest is what the build pulls),
`<NAME>_REF` with `<NAME>_COMMIT` beside it (the tag names the source,
the commit is what the build checks out and asserts), `<NAME>_VERSION`,
or `CUDA_MIN_DRIVER_MAJOR`; `env.load_pins` refuses any other key name,
a tag, digest, ref, or commit without its partner, and a commit that is
not 40 lowercase hex characters. `PYTORCH_DEVEL_TAG`, `PYTORCH_RUNTIME_TAG`,
`TORCHCODEC_VERSION`, and `CUDA_NPP_VERSION` must name one PyTorch
release and CUDA family (`env.cuda_family`); change the four together.

1. Write it in `pins.env` as a plain value (no quotes, no whitespace).
2. Add it to the `comfy` service's `build.args` in `compose.yaml` as
   `${KEY:?pin it in pins.env}` and to `services/comfy/Dockerfile` as a
   top-level `ARG KEY` without a default, redeclared in each stage that
   reads it, so that `pins.env` stays the only place the value lives;
   `test_pins_env_is_the_only_place_a_pinned_value_lives` fails
   otherwise. For a `<NAME>_REF`, the `RUN` that fetches it compares the
   checked-out `HEAD` with `<NAME>_COMMIT` and refuses a moved tag;
   `test_every_tag_fetch_asserts_the_pinned_commit` fails otherwise. The
   frontend is the exception: the wrapper passes `COMFYUI_FRONTEND_COMMIT`
   as `FRONTEND_GIT_REF`, which the frontend stage fetches and checks.
   `CUDA_MIN_DRIVER_MAJOR` is the one pin no build consumes; `doctor`
   reads it.
3. For a pin that `pins check` resolves, add one `RESOLVERS` entry
   keyed by the pin in `latentcrate/checkpins.py` and a case in
   `tests/test_checkpins.py`.

## Node, model, and workflow sets

The loaders enforce the manifest rules:

- A node entry is exactly `name`, `repository`, and `commit`: a
  credential-free HTTPS URL on a host in
  `config/custom-nodes/allowed-git-hosts.txt` and a full lowercase
  commit (`latentcrate/nodes.py`).
- A model file is a Hugging Face file at a full commit or a Civitai
  version, with a `destination`; a shipped set pins its `size` and
  `sha256`, a private set may omit them. The set carries at least
  one license with an immutable HTTPS URL and commit-pinned official
  workflow URLs (`latentcrate/models/manifest.py`).
- A workflow entry states its provenance
  ([Manifest keys](docs/workflows.md#manifest-keys)); review checks that
  a clone's license permits redistribution and, for a modified clone,
  modification. The gate requires every shipped workflow set to resolve
  against the shipped model and node sets (`tests/test_workflows.py`).
- An entry of `config/model-equivalents.toml` meets the rule in
  [Equivalent model files](docs/workflows.md#equivalent-model-files);
  review checks the source its `reason` cites.
- Every manifest carries a one-line `description`, and names and
  selections follow `latentcrate/names.py`.

[Node sets](docs/nodes.md#node-sets) holds the node-set procedure and
[Add a set](docs/models.md#add-a-set) the model-set procedure with
`models draft` and `models check --remote`, and
[Add a set](docs/workflows.md#add-a-set) the workflow-set procedure.

Do not commit models, tokens, `.env`, `build/`, `data/`, `local/`, or
`reports/`.

## Settled decisions

Each entry gives the decision, its reason, and when to reconsider it;
the linked page holds the mechanism. Challenge one only with evidence
against its reason.

- **One Python package behind the shim; Bash only inside the image.**
  The host side has one language, one test runner, and one error style;
  `bin/latentcrate` only finds an interpreter. Reconsider if a host step
  needs what Python 3.11 cannot do.
- **`init` takes no node set; run `nodes install <set>` after `init`.**
  `init` writes `.env` and the directories and reaches no network; the
  clone is `install`'s idempotent job ([Node
  sets](docs/nodes.md#node-sets)).
- **One `pins.env`; experiments are branches.** A second pin file would
  be a second source of truth for the same build. Reconsider if two pin
  sets must be built from one checkout at once.
- **Node dependencies are copied to `build/custom-node-requirements`
  and rewritten in the image.** The image is the reviewed artifact, so
  the runtime has no installer and no compiler
  ([Source and dependencies](docs/nodes.md#source-and-dependencies)).
  Reconsider if a node needs a dependency the offline wheel build cannot
  produce.
- **Builds and GPU validation on the GPU host only; CI is the
  engine-free gate.** The image has no CPU target and a build takes
  hours. Reconsider if a hosted runner with the GPU becomes affordable.
- **Kept features.** Four frontend modes, three Sage modes, the
  templates tools, WSL2 as an expected platform, `HOST_MODEL_GID` with
  `keep-groups`, and `MODEL_SET_EXTRA_WRITE_ROOTS` each have a user path
  (`help`, `.env.example`), so removing one is a cut to decide, not a
  cleanup. Reconsider one when a run shows it unused.
- **`up` runs in the foreground; `--detach` returns once ComfyUI is
  healthy or has failed.** The first start shows the logs, and no
  script can forget a readiness wait, so there is no `wait` command.
  Reconsider if a caller needs to return before the health check.
- **Base images are pinned by tag and manifest digest.** A tag can be
  moved; the digest is what the build pulls.
- **The CUDA architecture list and the Sage build jobs are settings,
  not pins.** They describe the host's card and cores, not a component
  version ([GPU and engine
  support](docs/configuration.md#gpu-and-engine-support)).
- **The validation page is a status table with dates, not a record.**
  A finding goes to the page that owns the fact
  ([Status](docs/validation.md#status)). Reconsider if a procedure
  needs a narrative no table row can hold.
- **Release frontend mode is a Git build at the pinned tag;
  `--frontend-dist` serves a prebuilt tree.** One code path and one
  image target serve release and Git mode; the trust chain is the
  commit, the pinned Node image, and the lockfile
  ([Modes](docs/frontends.md#modes)). Reconsider if the frontend build
  adds more than a few minutes to every default build.
- **The image tag is the variant and `COMFYUI_REF`.** A readable tag;
  `up` recreates the container when the image changes. A branch that
  wants a second image set sets `LATENTCRATE_IMAGE`. Reconsider if two
  pin sets of one ComfyUI version must coexist.
- **Source mode is two engine runs of the pinned Node image.**
  Lifecycle scripts run offline and the Dockerfile stays unchanged
  ([Source mode](docs/frontends.md#source-mode)). Reconsider if a
  Compose provider gains one build-context mechanism both engines honor.
- **Node sets install as idempotent per-node clones; `up` refuses a
  missing node.** An interruption leaves nodes absent, never
  half-written, and a second `install` completes it, so no journal is
  needed ([Node sets](docs/nodes.md#node-sets)). Reconsider if a set
  needs a cross-node transaction.
- **No `docs/cli.md`.** `help` is the syntax source; each command's
  usage line and semantics sit on the page that owns its topic.
  Reconsider if a command gains semantics no owner page can hold.
- **Pin grammar plus the family rule.** The grammar in [Add a
  pin](#add-a-pin) refuses URLs and image references, so repository
  names and the TorchCodec index live in the Dockerfile; a commit
  beside each ref lets the build refuse a moved tag; the one
  cross-check is the CUDA family the four PyTorch pins share.
  Reconsider if a pin needs a shape the grammar cannot name.
- **Verification stamps hold SHA-256, size, and `mtime_ns`, not
  inode or device.** Those two are synthesized on Windows-drive mounts
  under WSL2 and would force needless re-hashing
  ([How a set works](docs/models.md#how-a-set-works)). Reconsider a
  read-only `/models` mount once a run shows what Manager writes.
- **`models check [--remote]`.** Offline shape validation always;
  `--remote` reuses the Hub metadata request `models draft` needs to
  compare sizes and hashes without a download, and stays out of the
  gate. Reconsider if the Hub drops the metadata endpoint.
- **An `.env` key absent from `.env.example` is refused, naming
  `.env.example`; `HF_TOKEN` and `CIVITAI_TOKEN` are accepted.** An
  unlisted key is a typo or an engine variable that belongs in the
  shell. Reconsider when a documented need for pass-through appears.
- **The entrypoint policy is tested by `tests/test_entrypoint.py`
  driving the real `services/comfy/entrypoint.sh` through bash.** One
  test runner. Reconsider if the entrypoint gains behavior bash under a
  bare environment cannot reproduce.
- **`templates` refuses without the image instead of building it.** A
  build takes hours and a template listing must not start one; the
  refusal names `build`.
- **Private sets live in `local/model-sets/` and may omit `size` and
  `sha256`; the first download is trusted and recorded.** The author
  would compute both from the same download, so a pin adds nothing for a
  set nobody else fetches, while the shipped loader stays strict
  ([Private sets](docs/models.md#private-sets)). Reconsider if a private
  set is ever shared or a record-only file is found swapped.
- **Every workflow set mounts, read-only, always.** A Compose bind is
  static, so mounting only some sets would need a generated overlay, and
  the files are kilobytes; read-only keeps ComfyUI out of the checkout
  ([How a set works](docs/workflows.md#how-a-set-works)). Reconsider
  when a user asks to hide sets they do not use or to save edits back
  into a set.
- **Civitai is a source beside Hugging Face, pinned by version id, size,
  and SHA-256.** Many LoRAs and fine-tunes exist nowhere else, and the
  pins catch a re-upload
  ([Civitai sources](docs/models.md#civitai-sources));
  [Privacy](docs/privacy.md#what-runs-where) says where the token goes.
  Reconsider if deleted or re-uploaded versions break shipped sets
  often, or if the token leaves `civitai.com` and `civitai.red` on any
  path.
- **`smoke-gpu` proves function, not provenance.** Provenance is proven
  at build time by the pinned digests and commits, except the Python
  packages that resolve from PyPI at build time; the smoke test checks
  that the running image works
  ([Procedures](docs/validation.md#procedures)). Reconsider if a run
  shows an image that passes while built from the wrong inputs.
