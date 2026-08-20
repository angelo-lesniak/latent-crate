# Contributing

LatentCrate intentionally supports a narrow environment first. Changes should
preserve the portable Docker/Podman Compose core, host-mounted data model, and
independent backend/frontend pinned versions.

Before opening a change, install Bash, Python, Git, ripgrep, and ShellCheck,
then install the Python test dependencies with `python3 -m pip install -r
requirements-dev.txt`. Ensure Docker Compose or Podman Compose is available
when changing runtime behavior. The Python test dependencies include the
standalone podman-compose parser, so Compose-file compatibility does not require
a running Podman engine.

Keep `bin/latentcrate` limited to usage, option parsing, and command dispatch.
Shared CLI behavior belongs in the fixed Bash modules under `lib/latentcrate/`;
do not load implementation files from environment-controlled paths.

```bash
bash tests/static.sh
```

`tests/static.sh` runs the whole local gate: syntax and ShellCheck sweeps, the
Python unit tests, `tests/check-project.py` (repository invariants; requires
PyYAML), `tests/cli.sh`, `tests/node-deps-lifecycle.sh`, `tests/entrypoint.sh`
(container entrypoint policy, engine-free), `tests/doctor.sh` (host doctor
checks against the fake binaries in `tests/fixtures/doctor-bin/`), and
`tests/resolve-frontend.sh` (offline frontend-reference resolution against the
fake git in `tests/fixtures/resolver-bin/`), and `tests/podman-compose.sh`
(all Compose variants against podman-compose 1.6). Missing optional tools (ripgrep,
ShellCheck, a container engine) skip their component with an explicit
`SKIPPED:` line rather than silently passing. Set
`LATENTCRATE_STATIC_STRICT=1`, as CI does, to turn every such skip into a hard
failure that names the missing tool.

## Adding a pinned version key

A new pinned version key must be added in every one of these synchronized
locations, or builds and checks will disagree about it:

1. `versions/current.env` and `versions/edge.env` (every `versions/*.env`
   profile must define it).
2. The `comfy` service `build.args` block in `compose.yaml` — without a
   non-empty fallback default; the version profiles are the single source of
   pinned values.
3. `services/comfy/Dockerfile`: the top-level `ARG` (no `=default`) and an
   `ARG` re-declaration in each stage that consumes it.
4. Both `build-args` blocks in `.github/workflows/build.yml` (the `build` and
   `scheduled-edge` jobs).
5. `VERSION_KEYS` in `tests/check-project.py`, which then enforces the key's
   presence in every profile and the no-duplicate-default rules across the
   layers above for all registered keys.

GPU-related changes should include the report produced by:

```bash
bash bin/latentcrate smoke-gpu <profile> [--no-sage]
```

Changes to GPU, storage, engine, Manager, dependency, or frontend behavior should
also run the baseline and only the relevant feature checks in the Arch/NVIDIA or
WSL2/NVIDIA validation guide.

Do not commit models, datasets, generated outputs, `.env`, reports, or generated
custom-node dependency snapshots. Add a new trainer only when its Dockerfile,
Compose service, documentation, and basic verification are all present.

Third-party node-set entries must use a public credential-free HTTPS repository and
a reviewed full commit. Keep sets small and purpose-specific, explain why each
node belongs, and update dependency and GPU validation when changing a set.

Model-set entries must use immutable Hugging Face repository commits and include
the exact remote filename, byte size, SHA-256 checksum, license link, and a
commit-pinned official workflow links. Run the model-set unit tests and verify
remote metadata with `python3 scripts/verify-model-set-metadata.py` before
changing a shipped pin. This checks file metadata, official workflow links, and
license references using public metadata and no token. Do not add tokens or
downloaded weights to the repository.
