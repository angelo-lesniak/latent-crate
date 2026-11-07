# Contributing

LatentCrate intentionally supports a narrow environment first. Changes must
preserve the portable Docker/Podman Compose core, host-mounted data model, and
independent backend/frontend pinned versions.

## Layout

Keep `bin/latentcrate` limited to usage, option parsing, and command dispatch.
Shared CLI behavior belongs in the fixed Bash modules under `lib/latentcrate/`;
do not load implementation files from environment-controlled paths.

## Tests

Before opening a change, install Bash, Python, Git, ripgrep, and ShellCheck,
then install the Python test dependencies with `python3 -m pip install -r
requirements-dev.txt`. `tests/static.sh` also needs an executable named
`python` on `PATH`. Ensure Docker Compose or Podman Compose is available
when changing runtime behavior. The Python test dependencies include the
standalone podman-compose parser, so Compose-file compatibility does not require
a running Podman engine.

```bash
bash tests/static.sh
```

`tests/static.sh` runs the whole local gate: syntax and ShellCheck sweeps, the
Python unit tests, `tests/check-project.py` (repository invariants; requires
PyYAML), `tests/cli.sh`, `tests/node-deps-lifecycle.sh`, `tests/entrypoint.sh`
(container entrypoint policy, engine-free), `tests/doctor.sh` (host doctor
checks against the fake binaries in `tests/fixtures/doctor-bin/`),
`tests/resolve-frontend.sh` (offline frontend-reference resolution against the
fake git in `tests/fixtures/resolver-bin/`), `tests/export-node-set.sh` (the
node-set exporter), and `tests/podman-compose.sh` (all Compose variants against
podman-compose 1.6). Missing optional tools (ripgrep,
ShellCheck, a container engine) skip their component with an explicit
`SKIPPED:` line rather than silently passing. Set
`LATENTCRATE_STATIC_STRICT=1`, as CI does, to turn every such skip into a hard
failure that names the missing tool.

GPU-related changes should include the report produced by
`bash bin/latentcrate smoke-gpu`; see the
[CLI reference](docs/cli.md#smoke-gpu) for its flags.

Changes to GPU, storage, engine, Manager, dependency, or frontend behavior should
also run the baseline and only the relevant feature checks in the Arch/NVIDIA or
WSL2/NVIDIA validation guide.

## Documentation changes

Write and review documentation with the `documentation-guidelines` skill
from [SkillCrate](https://github.com/angelo-lesniak/skill-crate): copy
`skills/documentation-guidelines` from that repository into your agent's
skills directory. This section is the checklist the skill asks for.

Pages in `docs/` are flat kebab-case Markdown files, hard-wrapped near 80
columns. [`docs/index.md`](docs/index.md) is the hand-maintained hub: add
a new page to exactly one of its sections, "First setup", "Everyday
tasks", or "Maintenance and validation". The README's "Start here"
carries the short first path and `docs/getting-started.md` the full
setup; do not write a third copy of the setup steps.

The main sources are the usage text (`bin/latentcrate help`) for command
syntax, `.env.example` for settings and defaults, `pins.env` for pins,
and the manifests in `config/model-sets/` and `config/custom-nodes/sets/`
for set contents.

Each fact has one owner page; update it instead of restating the fact
elsewhere:

- `README.md#start-here` holds the install and the quick start;
  `docs/getting-started.md` the full setup.
- `docs/cli.md` explains every command and flag; other pages link to it
  instead of restating flags. The usage text stays the syntax source.
- `docs/configuration.md` holds the settings.
- `docs/storage.md` holds the host directories and model storage, and
  `#removing-latentcrate-and-reclaiming-disk-space` the removal.
- `docs/upgrades.md` holds pin updates and updating LatentCrate itself.
- `docs/model-sets.md` holds model sets, Hugging Face access, and
  licenses.
- `docs/templates.md` holds the official template tools and model-set
  manifest drafts.
- `docs/third-party-nodes.md` holds Manager installs, node sets, private
  nodes, and saved node dependencies.
- `docs/sageattention.md` holds SageAttention.
- `docs/frontends.md` holds the frontend modes.
- `docs/gpu-support.md` holds GPU and container-engine support.
- `docs/build-flow.md` holds the image stages and targets.
- `docs/ci.md` holds CI.
- `docs/privacy.md` holds the privacy defaults and their limits.
- `docs/troubleshooting.md` holds observed failures, one section each.
- `docs/validation-status.md` is the only statement of what is validated
  and defines the status and evidence vocabulary;
  `docs/validation-history.md` holds the dated records;
  `docs/arch-nvidia-validation.md` and `docs/wsl2-nvidia-validation.md`
  hold the procedures.
- `docs/glossary.md` is the reader glossary; the project has no word
  list.
- `docs/trainers.md` holds the trainer plans.

Record each user-visible change in `CHANGELOG.md` under `## Unreleased` in the
matching `### Added`, `### Changed`, or `### Fixed` subsection.

Run `bash tests/static.sh`, or `python3 tests/check-project.py` (requires
PyYAML) for a documentation-only change. Its link check resolves relative
file links in every Markdown file but not heading anchors; check anchors
and the facts in the prose by hand.

## Add a pin

`pins.env` is the only place a pinned value of `services/comfy/Dockerfile`
or `services/tools/Dockerfile` lives. A new build pin goes in each of these
places:

1. `pins.env`: one `KEY=value` line with a bare value.
2. `compose.yaml`, in the `build.args` of every service that builds the
   Dockerfile consuming the pin: the `comfy` service for
   `services/comfy/Dockerfile`, the `x-tools-build-args` block for
   `services/tools/Dockerfile`, as
   `KEY: "${KEY:?pin it in pins.env}"`. No Compose file, Dockerfile, or
   wrapper module gives a pin a fallback.
3. The Dockerfile: the top-level `ARG KEY` without a default, and an
   `ARG KEY` re-declaration in each stage that consumes it. A base image
   of either Dockerfile is pulled by a `*_TAG` and `*_DIGEST` pin pair.

`tests/check-project.py` fails until steps 1, 2, and an `ARG KEY` line
agree; check the stage re-declarations by hand. A pin that both
Dockerfiles consume, such as `FRONTEND_NODE_TAG`, goes in both. A new pin
of `services/tools/Dockerfile` also goes in the `--build-arg` list of the
model-set helper build in `.github/workflows/static.yml`. A pin that is
not a build argument, such as `CUDA_MIN_DRIVER_MAJOR`, goes in
`NON_BUILD_PIN_KEYS` in `tests/check-project.py` instead of steps 2 and 3.

## Node sets, model sets, and trainers

Do not commit models, datasets, generated outputs, `.env`, reports, or generated
custom-node dependency snapshots. Add a new trainer only when its Dockerfile,
Compose service, documentation, and basic verification are all present.

Third-party node-set entries must use a public credential-free HTTPS repository and
a reviewed full commit. Keep sets small and purpose-specific, explain why each
node belongs, and update dependency and GPU validation when changing a set.

Model-set entries must use immutable Hugging Face repository commits and include
the exact remote filename, byte size, SHA-256 checksum, license link, and
commit-pinned official workflow links. Run the model-set unit tests and verify
remote metadata with `python3 scripts/verify-model-set-metadata.py` before
changing a shipped pin. This script checks file metadata, official workflow
links, and license references using public metadata and no token. Do not add
tokens or downloaded weights to the repository.

`bash bin/latentcrate templates create-model-set` can create a reviewable
model-set manifest draft from an installed official template; see the
[CLI reference](docs/cli.md#templates) and
[official template tools](docs/templates.md) for the draft and review steps.
