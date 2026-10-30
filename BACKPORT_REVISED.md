# LatentCrate port plan (revised 2026-10-28)

Written against LatentCrate `main` at 5588f7f (unchanged since 2026-08-26)
and TokenCrate `main` at 5e7b735 (2026-10-22). It replaces the plan of
2026-09-25 (its text is no longer on disk; its decisions are in
[section 2](#2-settled)) and folds in the parallel 2026-10-28 rewrite of
`BACKPORT.md`, whose lessons table and decision protocol agree with this
plan; where the two differ, [section 3](#3-decisions-for-the-grilling-session)
carries the choice; the seven challenges of the 2026-10-28 Codex review
of this plan are resolved where each applies (3.4, 3.8, 4.1, 4.3, 5,
6.2, 7). Mantra: code is a liability; less is more; a correct
redesign over a patch. Claims are CONFIRMED (read or measured) or
SUSPECTED (inferred or estimated); LatentCrate line references are exact
for 5588f7f, TokenCrate references name the module or section.

A plan may describe what exists and what changes. Repository
documentation may not: package E states what the shipped tree does and
never what it replaced. Delete this file when the work is done; its
decisions live on in `CONTRIBUTING.md` "Settled decisions".

## 1. Verdict

The target is the same product with TokenCrate's structure: one
standard-library Python package `latentcrate/` behind a Bash shim, one
`pins.env`, one Compose service plus small overlays, a Dockerfile that
proves its own pins, a gate that parses artifacts instead of grepping
them, and seven documentation pages.

| Measure | LatentCrate 5588f7f | TokenCrate 5e7b735 | Status |
| --- | ---: | ---: | --- |
| Tracked files / lines | 136 / 19,028 | 184 / 48,990 | CONFIRMED |
| Host implementation | 1,855 lines Bash + 3,542 lines helper Python | 8,397 lines Python in `tokencrate/` (1,350 of them `stats/`, which has no counterpart here) | CONFIRMED |
| Compose services | 12 in `compose.yaml` + 3 overlays | 9 (1 always on, 8 behind profiles) + 4 overlays | CONFIRMED |
| Prose | 26,081 words, 24 pages under `docs/` | 23,282 words, 8 pages | CONFIRMED |
| Pin files | 2 profiles, 19 keys each | 1 file, key grammar enforced | CONFIRMED |

Expected after the port (SUSPECTED): about 4,700 lines of package code
(TokenCrate's `cli`, `env`, `names`, `engine`, `doctor`, `checkpins`, and
`models/` are 3,685 lines before any ComfyUI-specific module), about
11,000 tracked lines in all, and about 7,500 words of prose.

## 2. Settled

Taken on 2026-09-25, not reopened:

1. The host side is one standard-library Python package behind the shim;
   Bash only inside the image.
2. One `pins.env`, seeded from `versions/edge.env`; experiments are
   branches.
3. Node dependencies: the host copies each node's requirement files into
   `build/custom-node-requirements`, the `node-deps-builder` stage
   validates and rewrites them.
4. Image builds and GPU validation on the GPU host only; `build.yml`
   deleted; CI keeps the engine-free gate and hadolint.
5. Kept: four frontend modes (the mechanism of two of them is open in
   3.1 and 3.3), three Sage modes, the templates tools, WSL2 as an
   expected platform, `HOST_MODEL_GID` with `keep-groups`,
   `MODEL_SET_EXTRA_WRITE_ROOTS`.
6. `up` runs in the foreground by default with `--detach`.

Accepted here because they are TokenCrate's shipped rules and nothing in
LatentCrate argues against them:

7. Base images pinned by tag and manifest digest. Docker Hub's
   `pytorch/pytorch` tags publish a single-arch image manifest digest
   (CONFIRMED live for `2.13.0-cuda13.0-cudnn9-runtime`), which pulls by
   `@sha256` on both engines; the `pins.env` comment says "manifest
   digest" without assuming a manifest list.
8. The CUDA architecture list and the Sage build jobs are `.env`
   settings, not pins.
9. The validation page is a status table with real dates per procedure,
   host, and engine, a known-gaps list, the procedures, and "Recording a
   run"; no narrative records; findings go to the page that owns the
   fact. The two validation history pages are deleted; Git keeps them.

## 3. Decisions for the grilling session

Each item changes the shape of a package. Format: the context a
decision needs, the options with consequences, the recommendation, and
the maintainer's answer. Items without a materially different
alternative are in [4.5](#45-cuts-taken-in-this-revision) with a
one-line reason and can be vetoed there.

All ten were decided in the grilling session of 2026-10-28; every answer
is the recommended option, with 3.1 confirmed after Run 1's measurement
and 3.9 narrowed to three stamp fields. Package E records them as
settled decisions.

### 3.1 Release frontend: archive or Git tag

Context: release mode downloads `dist.zip` of the pinned frontend tag,
checked against `COMFY_FRONTEND_DIST_SHA256`, through
`services/comfy/install-release-frontend.py` (165 lines, stays in the
image under option a) and `frontend pin-release`, which fetches the
archive to print its digest (frontend.sh:38-42). Git mode builds the
frontend from a resolved commit with pnpm (Dockerfile:606-655), a
lockfile install from npm inside the build. The pinned reference
`Comfy-Org/ComfyUI_frontend@v1.53.1` is a Git reference already.
CONFIRMED. The pnpm build time on the host is not measured; Run 1
([section 9](#9-host-validation)) builds `--frontend-git` once so the
decision has the number.

- (a) Keep the archive: the fastest default build, one SHA-256 pin, the
  installer, and the digest download inside `pins check`. The trust
  chain is one GitHub release asset pinned by digest.
- (b) Release mode is Git mode at the tag: deletes the archive pin, the
  installer, the download, one code path and one image target; every
  default build pulls the pinned Node image and runs a lockfile install
  and a pnpm build. The trust chain is the Git commit plus the pinned
  Node image plus the lockfile.
- (c) As (b), with `--frontend-dist` kept for a prebuilt tree.

Recommendation: (c), decided after Run 1's measurement. Reopen if the
build adds more than a few minutes to every default build.

Decided: (c), subject to Run 1's timing.

### 3.2 Image tag

Context: the 2026-09-25 plan tagged images `<variant>-<12 hex of a digest
over pins.env>` so that images of different pins coexist; the 2026-10-28
rewrite keeps "selections coexist". TokenCrate tags by the primary pin
(`tokencrate/llama:${LLAMA_CPP_TAG}`, CONFIRMED) and LatentCrate's
`assert_running_image` (runtime.sh:13-29) compares image IDs, which
covers every pin.

- (a) Digest tag: coexistence for any pin change; a tag nobody can read;
  every pin bump leaves a 20 GB image under a hash tag until pruned.
- (b) `latentcrate/comfy:<variant>-<COMFYUI_REF>`: readable; a rebuild
  within one ComfyUI version replaces the tag and the old image ID stays
  until pruned, as on any rebuild; `up` recreates the container when
  the image ID or a config-digest label changes.

Recommendation: (b). A branch that wants a second image set sets
`LATENTCRATE_IMAGE`.

Decided: (b).

### 3.3 Source mode: where the frontend checkout is built

Context: today `services/tools/build-local-frontend.sh` runs two phases
in two helper containers: a networked `pnpm install --frozen-lockfile
--ignore-scripts` from an empty tool home, then `pnpm build --offline`
with no network (build-local-frontend.sh:24, 141-156, CONFIRMED). The
2026-09-25 plan replaced this with a Dockerfile stage fed by a named
additional build context; `build/custom-node-requirements` already
reaches the build as a bind mount from the main context
(Dockerfile:436), so that plan's second context was never needed.

- (a) The Dockerfile stage: two more image targets, a dependency on both
  providers' additional-context handling (podman-compose translates it
  to `--build-context`, Docker Compose 5 builds through bake), network
  during the build, and an open `.dockerignore` question for a checkout
  with `node_modules`.
- (b) One networked `engine run` of the pinned Node image that installs
  and builds in one go, writing a dist tree the dist overlay serves.
  Smallest, but lifecycle scripts run with the network up, which the
  current helper prevents.
- (c) Two `engine run`s of the pinned Node image from `variants`: the
  install with `--ignore-scripts` and the network, then the build with
  `--network none`, same flags as today, output to a dist tree the dist
  overlay serves. No Dockerfile change, the trust boundary unchanged.

Recommendation: (c). With 3.1 (c) the build-time frontend inputs are
`git` and `dist` only; `source` is a host step that ends in `dist`.

Decided: (c).

### 3.4 Node set install: journal or idempotent clones

Context: `scripts/manage-node-set.py:278-471` stages an install under
`.latentcrate-node-set-*.disabled` with a journal, recovery of an
interrupted transaction, and an entrypoint refusal
(entrypoint.sh:104-121), about 200 lines. Promise: never a half-installed
set.

- (a) Port the transaction as is.
- (b) Clone each node into a temporary sibling and rename it into place
  (atomic per node); `install` is idempotent; `status` lists what is
  missing or at the wrong commit; `sync` re-clones a wrong node (one
  clone per wrong node, the cost of this option); an interruption
  leaves some nodes absent and a second `install` completes it. The
  "stop ComfyUI first" rule stays. No journal, no entrypoint branch.
  `up` refuses when a directory of the selected set is missing (one
  `stat` per node, no Git, no network) and names `nodes install`, so a
  half-installed set never starts; a node at the wrong commit is a
  `nodes status` matter, not a refusal.

Recommendation: (b).

Decided: (b), with the `up` refusal on a missing node.

### 3.5 `wait` as a command or as `up`'s own behaviour

Context: `wait` polls the health check after `up --detach`
(bin/latentcrate, runtime.sh). TokenCrate's `up` carries `--timeout` and
returns when the service is ready or has failed (CONFIRMED,
`cli.py` `COMMANDS`); it has no `wait`. The 2026-10-28 rewrite lists
`wait` among the preserved commands.

- (a) Keep `wait`: `up --detach` returns at once; scripts call `wait`.
- (b) `up --detach` returns when ComfyUI is healthy or has failed,
  bounded by `--timeout`; no `wait` command.

Recommendation: (b): one fewer command and no script can forget the
wait.

Decided: (b).

### 3.6 Landing order

Context: the image build is the only part that needs the GPU host and
hours per attempt. Landing everything as one cut validates it last.

- (a) One cut: one review, one commit series, the Bash wrapper stays
  until the end.
- (b) Two cuts: first the image, `pins.env`, the `comfy` service and the
  GPU overlay, built and smoked on the host while the Bash wrapper and
  the helper services still drive everything else; then the package,
  tests, and documentation.

Recommendation: (b). [Section 8](#8-plan) is written for it.

Decided: (b).

### 3.7 `docs/cli.md`: keep or fold into `help`

Context: TokenCrate keeps `docs/cli.md` with every usage line verbatim
(contract-tested) plus the semantics `help` cannot carry. LatentCrate's
`cli.md` is 2,494 words, mostly flag tables that duplicate the usage
text. The documentation rule says syntax has one source.

- (a) A short `cli.md`: usage lines verbatim plus one paragraph of
  semantics per command; no flag tables.
- (b) No `cli.md`: `help` is the syntax source; each command's semantics
  sit on the page that owns the topic, which shows that command's usage
  line verbatim (contract-tested, so the line still has one source and
  one copy).

Recommendation: (b): about twelve commands after the port, each with a
natural owner page. Reopen if a command gains semantics no owner page
can hold.

Decided: (b).

### 3.8 Pin grammar

Context: TokenCrate's `env.load_pins` accepts `<NAME>_TAG` with exactly
one partner `<NAME>_DIGEST`, `<NAME>_VERSION`, and
`CUDA_MIN_DRIVER_MAJOR`, with bare-token values (CONFIRMED, `env.py`).
LatentCrate's 19 pins include full image references, an index URL, and
`owner/repo@ref` references, which that grammar refuses.

- (a) Extend the grammar to LatentCrate's shapes (`_IMAGE` with
  `_DIGEST`, `_REF`, `_SHA256`, `_URL`) and port the two cross-checks
  (TorchCodec wheel suffix matches the index URL, NPP major matches the
  CUDA family) as `load_pins` rules.
- (b) Shrink the pins to the grammar plus `_REF`: `PYTORCH_DEVEL_TAG`
  and `PYTORCH_RUNTIME_TAG` each with its digest, repository names in
  the Dockerfile, the TorchCodec index URL derived in the Dockerfile from
  the CUDA suffix of the tag (the cross-check demands they agree, so it
  is a function, not a pin), `_SHA256` only if 3.1 keeps the archive.
  The URL check disappears with the URL. One `load_pins` rule stays,
  because three pins still name a CUDA family independently: the
  `cuNNN` of the PyTorch tag, the `+cuNNN` suffix of
  `TORCHCODEC_VERSION`, and the major of `CUDA_NPP_VERSION` must agree
  (one function, one refusal sentence, one test; today's
  check-project.py:223-225 is the source).

Recommendation: (b).

Decided: (b), with the one family rule.

### 3.9 Model verification stamps on a writable mount

Context: TokenCrate writes `<file>.tokencrate-verified` (SHA-256, size,
mtime, inode, device) after a verified download so that `models status`
does not re-hash hundreds of GB; `--verify` re-hashes. Its library is
read-only inside the container. LatentCrate mounts `/models` writable
(compose.yaml:142-143, no `read_only`; CONFIRMED) because ComfyUI and
Manager write there, which is why `MODEL_SET_EXTRA_WRITE_ROOTS` exists.

- (a) Stamps as TokenCrate's: a file whose size, mtime, inode, or
  device changed is re-hashed; a same-size same-mtime overwrite is not
  detected without `--verify`. The threat model (`SECURITY.md`: one
  trusted user) does not include that case.
- (b) No stamps: `status` re-hashes every file every time (minutes per
  44 GB set, SUSPECTED), as `manage-model-sets.py` does today.
- (c) Stamps, and the `comfy` service mounts `/models` read-only with
  `MODEL_SET_EXTRA_WRITE_ROOTS` as the only writable binds, so the
  container cannot touch a verified file. Changes what Manager and
  ComfyUI can save; the extra roots setting already describes the
  writable subset.

Recommendation: (a) now; (c) when a run shows which directories ComfyUI
and Manager write to. Either way the stamp check runs before `up`
mounts the library.

Decided: (a), with the stamp reduced to SHA-256, size, and `mtime_ns`.
Inode and device numbers stay out because they are synthesized on
Windows-drive mounts under WSL2 and may not survive a reboot, which
would force a needless re-hash; `status --verify` is the remedy for a
same-size timestamp-preserving swap. TokenCrate's `models/library.py`
applies the same rule (changed on branch `refactor/reduce`,
2026-10-28). Run 2 records whether the shipped node pack or Manager
writes into `/models`; a read-only mount is a follow-up, not part of
the port.

### 3.10 Model metadata check for authors

Context: `scripts/verify-model-set-metadata.py` (106 lines) asks the Hub
API for each pinned file's size and hash (`revision/<commit>?blobs=true`)
and checks that the workflow and license links answer, without a
download. The 2026-09-25 plan deleted it because `models fetch` fails
loudly on a wrong pin; the 2026-10-28 rewrite says deletion needs a
decision.

- (a) Delete it; a wrong pin fails at `fetch`, which is TokenCrate's
  rule.
- (b) `models check [--remote]`: offline shape validation always;
  `--remote` reuses `hub`'s metadata request (which `models draft` needs
  anyway) to compare size and hash and to `HEAD` the two links. About
  40 lines over `draft`'s code (SUSPECTED), no download, and it stays
  out of the offline gate.

Recommendation: (b).

Decided: (b).

## 4. Target design

### 4.1 Package layout

| Module | Responsibility | Source |
| --- | --- | --- |
| `cli` | `COMMANDS` table of `Command` and `Flag` entries; `USAGE`, dispatch, and the misplaced-flag refusals generated from it; signal handlers; stdout flush and `BrokenPipeError` | TokenCrate `cli.py`; usage from bin/latentcrate:33-104 minus profiles and the cut commands |
| `env`, `names` | `.env.example` defaults, `.env` overrides (mode 0600, owned by the caller, an empty path setting refused rather than defaulted), `pins.env` under the 3.8 grammar, `Settings.storage` as the one list of host paths with their rules (mounted, writable, created by `init`, no overlap, none containing the checkout), one manifest reader (`schema = 1`, no unknown keys, path components without `.` or `..`), one list parser | TokenCrate `env.py`, `names.py` |
| `files` | Atomic writes (temp file beside the target, `os.replace`) | TokenCrate `files.py` |
| `engine`, `localhttp` | `Selection` shared by `doctor` and `up` (Podman first, rootless with crun, the Compose provider parsed, the Docker Compose plugin under Podman refused because it drops CDI devices, no `.exe` client under WSL); one lock `build/locks/<project>.lock` recording holder PID and command, held by `up` and `build` from the input copy to the start (one service, one project: nothing else needs the lock while an image builds; `status` and `logs` take none, `models fetch` has its own), by `down`, and by `nodes` commands; `build/stack.json` recording the engine at `up` so a disagreeing `CONTAINER_ENGINE` is refused until `down`; `require_port` (an occupied port is accepted only when this project's own service publishes it); local health requests that bypass host proxies | TokenCrate `engine.py`, `localhttp.py`; core.sh:163-176 |
| `variants` | `(frontend mode, sage mode)` to Dockerfile target, tag, and overlay; Git reference resolution with hardened Git; dist tree validation; source mode as the two `engine run`s of 3.3 (c) | frontend.sh:59-166, resolve-frontend.sh, TokenCrate `skills.git_environment` |
| `runtime` | `init`, `up`, `build`, `down`, `status`, `logs`, `smoke-gpu`; the requirement-file copy into `build/custom-node-requirements/` with `manifest.txt`, written to a staging sibling and renamed into place under the lock the build then keeps, so no copy, `nodes` command, or `down` runs while an image reads it; the `up` refusal on a missing node of the selected set (3.4); the running-image assertion; `up --timeout` under 3.5 (b) | bin/latentcrate:264-400, runtime.sh, node-deps.sh reduced to the copy |
| `models`: `manifest`, `library`, `hub`, `download`, `drafts` | Manifests with `destination` per file (LatentCrate uses it) and the workflow and license links; the library with three-field stamps (3.9); `hub.open_url` as the only place the token travels, redirects included; the streamed download with Range resume, free-space check, staging, size and checksum verification, atomic publication, a wrong existing file never overwritten; `models draft hf:<owner>/<repo>:<file>`; `models check` under 3.10 | TokenCrate `models/`; manage-model-sets.py:87-263 for the manifest fields and `allowed_roots` |
| `nodes` | `list`, `install`, `sync`, `status`, `check`; `export`; per-node atomic clones under 3.4 (b); hardened Git; the allowlist file; the lock held for the command's duration; refusal while ComfyUI runs; `export` reuses the module's validators (origin normalised to a public credential-free repository, no symlink, no execution-capable local Git configuration, clean tree) and prints a manifest, skipping a node with the reason | scripts/manage-node-set.py, export-node-set.sh |
| `templates` | `templates list` and `create-model-set` as `compose run --rm --no-deps --no-build --entrypoint python comfy …`, inheriting the service's hardening (podman-compose has `--no-build`, CONFIRMED); refuses when the image is missing and names `build` | templates.sh; manage-templates.py stays in the image |
| `doctor` | Facts, then judgement: the engine selection, driver major, compute-capability coverage against the arch list, one CDI fact (a spec directory holds `nvidia.com/gpu`), free kilobytes on the engine's storage root, `Settings.storage` iterated, the Docker model GID, bind address, port, one WSL warning, the x86-64 refusal | TokenCrate `doctor.py`; doctor.sh:124-362 |
| `checkpins` | Print-only `pins check`: one `RESOLVERS` entry per pin, `(no resolver; look it up by hand)` for the rest, one release link per pin, no release notes; Docker Hub listing narrowed to the CUDA family and bounded by the anonymous page limit | TokenCrate `checkpins.py`; update-versions.py:54-101 for the sources |

Rules from TokenCrate's `AGENTS.md` apply: a refusal is one sentence
naming cause and remedy; wording two commands can report lives in one
constant; no abstraction for one caller; a behaviour change arrives with
its test and its owner-page line. A broken manifest never stops `init`
or `doctor`. Dependencies point one way: `files`, `names`, `env` are
leaves; `cli` composes at the boundary.

### 4.2 Files

**`pins.env`**: plain `KEY=VALUE`, every key once, the 3.8 grammar,
self-describing (the suffix says what the value is and what checks it),
seeded from versions/edge.env. Not pins any more: `TOOL_PYTHON_IMAGE`,
`COMFY_BUILD_TARGET`, `LATENTCRATE_TAG` (derived), the arch lists and
`SAGE_BUILD_JOBS` (settings), and whatever 3.1 and 3.8 remove.

**`.env.example`**: the settings reference, and its first line says so.
Each setting carries its default, what it does, and which command
applies a change. `init` writes a comment-only `.env` with a header
explaining the defaults layer and sets mode 0600 on every run. The shim
exports `HOST_UID` and `HOST_GID` from the calling user; they are not
settings. Added: `COMFY_CUDA_ARCH_LIST=12.0`, `SAGE_BUILD_JOBS=2`.
`COMFY_BIND_ADDRESS` accepts `127.0.0.1` and `[::1]` without
`COMFY_ALLOW_REMOTE`; `localhost` is not a binding Docker Compose
accepts. `MODEL_SET_EXTRA_WRITE_ROOTS` names host paths the downloader
checks; the instruction to edit `compose.yaml` goes.

**Compose**: `compose.yaml` holds the `comfy` service with `user:` inside
it, every setting as `${VAR:?message naming who exports it}`, the pins
as `build.args` without fallback, and nothing else. `compose.gpu.yaml`
holds the `devices:` request so that `templates`, a CPU render, and
`--allow-no-gpu` need no CDI. `compose.podman.yaml` keeps `userns_mode:
keep-id` and `group_add: [keep-groups]`; `compose.docker.yaml` keeps
`group_add: [${HOST_MODEL_GID:?}]`; `compose.frontend-dist.yaml` stays.
`.dockerignore` is an allowlist (`*`, then `!` for what the build
needs), so `data/` never enters the context.

**Dockerfile**: `services/comfy/Dockerfile` pins every `FROM` as
`<image>:<tag>@sha256:<digest>`, has no `ARG` default for a pin, proves
its own pins where a build can (the ComfyUI and frontend commits and
the Sage version as labels; the TorchCodec decode and the NVCC-absent
check stay), and gains one `RUN` in `node-deps-builder` that runs the
in-image `rewrite-node-requirements.py` over the bind-mounted
`build/custom-node-requirements` tree before the toolchain installs.
One build argument carries the SHA-256 of `manifest.txt`, so that a
changed requirement file invalidates the stage on Buildah too (SUSPECTED
that Buildah does not content-address a bind mount from the context;
the copy step computes the digest anyway). `services/tools/Dockerfile`
goes in package C.

### 4.3 Commands

| After the port | Note |
| --- | --- |
| `init` | directories from `Settings.storage`, comment-only `.env` at 0600 |
| `doctor [--allow-no-gpu] [--sage mode]` | |
| `up [--sage mode] [--detach] [--timeout seconds] [--model-set name]... [frontend flag]` | copy requirement files, `compose build comfy`, `compose up --no-build`, poll until healthy or failed |
| `build`, `down`, `status`, `logs`, `smoke-gpu` | `smoke-gpu` tests GPU function (CUDA, Sage import, TorchCodec decode) and the running image ID; provenance is proven at build time |
| `models list\|fetch\|status [--verify]\|check [--remote]\|draft` | |
| `nodes list\|install\|sync\|status\|check\|export` | `export` prints a manifest for the clean public checkouts under `custom_nodes` |
| `templates list\|create-model-set` | on the built image; refuses without it |
| `pins`, `pins check [name]` | print only |

Removed: the profile argument, `config` (the gate renders through the
fake engine), `shell` (`podman exec -it <name> bash`, one line in
troubleshooting), `frontend pin-release`, `versions update`, `node-deps
snapshot|clear`, `--use-saved-node-deps`, `smoke-gpu`'s 19 `LATENTCRATE_EXPECT_*`
variables, `LATENTCRATE_SKIP_PLATFORM_CHECK`; `wait` under 3.5 (b).

### 4.4 Behaviours that must survive, with the test that proves each

Ctrl+C on a foreground `up` stops the container and leaves the project
removable by `down`; `down` removes only project-owned resources and
works when the render or the pins are broken; a second `up` while one
runs waits on the lock and says who holds it; `nodes install` and
`sync` refuse while ComfyUI runs and hold the lock while they work, so
they wait for a build and a build waits for them, and the node sources
a start launches are the ones its image was built from; `up` refuses a
selected set with a
missing node directory and names `nodes install`; a readiness
failure names the log; engine switching with a running stack is
refused; a download interrupted at any point resumes and a checksum
mismatch leaves no published file; a `.env` with another owner or wider
mode is refused.

### 4.5 Cuts taken in this revision

One line each because the alternative is not materially different; veto
by name.

- `config`, `shell`: see 4.3.
- `templates` refuses when the image is missing instead of building it
  for hours (templates.sh:35 builds first).
- `pins check` prints one link per pin and no release notes; ten
  components (`tool-python` goes with the tool image).
- `smoke-gpu` proves function, not provenance.
- Two CDI checks become one fact; two WSL warnings become one.
- The contracts test drops "`.env.example` defaults agree with Compose
  fallbacks" (the rule is the opposite: no fallbacks), "every service
  runs as the host user" (one service), "manifests pinned" (loader
  validation), and "Manager defaults" (the file is the source).

## 5. Tests and CI

`tests/static.sh` is TokenCrate's: `bash -n` and ShellCheck over the
shell that remains, an import of each package module and subpackage on
its own, `python3 -m unittest discover`, Ruff, `tests/podman-compose.sh`
with the fake engine over every Compose file combination the wrapper
passes (device, port, mounts, labels), the host-path scan (`/home/<x>`,
`/Users/<x>`, `/mnt/hostshare`, working in a clean copy of the tree:
`git ls-files --cached --others --exclude-standard` copied to a scratch
directory, since nothing is committed during the port), and
a Compose render per installed engine. Strict mode fails on any
`SKIPPED:` line and on a skipped unit test; PyYAML is mandatory. The gate
reads tracked files only, so `local/` node roots never enter it.

Deleted: tests/cli.sh, doctor.sh with `fixtures/doctor-bin/`,
entrypoint.sh, export-node-set.sh (its cases move to `test_nodes.py`),
frontend-release-pin.sh,
node-deps-lifecycle.sh, resolve-frontend.sh with `fixtures/resolver-bin/`,
version-update.sh, `fixtures/fake-bin/`, check-project.py (1,245 lines),
test_update_versions.py (replaced by `test_checkpins.py`),
test_release_frontend.py under 3.1 (c), `fixtures/frontend-source/`
under 3.3 (c) if the source test needs no checkout. Kept:
test_node_package_lock.py, test_node_resolution.py, test_templates.py
(scripts that stay in the image), test_snapshot_node_deps.py reduced to
the rewrite cases, `fixtures/custom-nodes/`, `fixtures/frontend-dist/`,
`fixtures/model-sets/`, `fixtures/podman-compose-bin/` (renamed to the
fake engine). Rewritten: test_model_sets.py and test_node_sets.py into
`test_models.py` and `test_nodes.py`. CONFIRMED as inventory.

New: `tests/support.py` (`Scratch` with a fixed copy list, scratch HOME
and storage, fake engine on `PATH`, free port, `run` through `python -m`
and `run_shim` through bash, local HTTP server), one `test_<module>.py`
per module with the cases of 4.4, and `tests/test_project_contracts.py`
that parses each artifact and fails with the rule, pinning absence as
well as presence: the `comfy` service's allowed keys, bind targets, and
published address with no fallback for a required setting; each
overlay's exact keys; pins only in `pins.env` (Compose `args:` and
Dockerfile `ARG` without defaults); no duplicate YAML keys; the token
never reaches Compose; the entrypoint's argument policy; every
`.env.example` key carries a comment line and every manifest a
description (the source documents itself; a page names a key only
where a reader acts on it, and no test demands inventories in prose);
every command's usage line verbatim on its owner page (3.7 b);
relative links and anchors resolve; the README table links every page;
every `requirements-dev` ecosystem covered by `dependabot.yml`.

CI: `static.yml` on Python 3.11 (the floor), strict, SHA-pinned actions,
`permissions: contents: read`, `timeout-minutes`, hadolint over the
Dockerfile; `build.yml` deleted; no CPU integration workflow (the image
has no CPU target; the GPU host is the integration environment).
`requirements-dev.txt` adds `ruff` and `PyYAML`, keeps `packaging` for
the rewrite tests. Dependabot: pip monthly, actions weekly grouped, a
comment on why no `docker` entry. The bug template asks for `bash
bin/latentcrate pins` and `git rev-parse --short HEAD`.

## 6. Documentation

### 6.1 The rule

The shared `documentation-guidelines` skill applies, and on top of it:

- Never two sources of truth. Syntax lives in `help` and one verbatim
  copy on the owner page; settings and defaults in `.env.example`; pins
  and their meaning in `pins.env`; set contents in the manifests
  (`models list`, `nodes list`); pin rationale in comments of
  `vcs-pins.toml` and the replacements file; the stage map in the
  Dockerfile header; what the gate runs in `tests/static.sh`'s output. A
  page links to the source and does not restate it. `AGENTS.md` states
  this rule in the same words as TokenCrate's, so agents in both
  repositories read one guardrail.
- No code documentation: a page never explains what a module, script,
  or stage does when reading it shows that.
- A page exists for a named reader with a decision: what a user needs
  initially (the README's "Start here") and where the deep dive is (the
  "Choose what to do next" table, one row per page). Pages hold the glue
  no single file shows ([6.3](#63-glue-knowledge-the-pages-must-keep)).
- Validation as settled in 2.9.
- Nothing relative to an earlier tree. Package E greps for `since`,
  `until`, `formerly`, `no longer`, `now`, `current`, `renamed`, `gone`,
  `rerun`, `tested tree` before finishing.

### 6.2 Page set

Seven pages under `docs/` and five root files; about 7,500 words
(SUSPECTED) against 26,081 today. The page set and the word counts are
targets for package E, not gates: the reader task in each row and the
[6.3](#63-glue-knowledge-the-pages-must-keep) list decide where a page
splits or merges, and no test counts words or pages.

| Page | Reader and decision | Holds | Must not hold |
| --- | --- | --- | --- |
| `README.md` (~900) | a ComfyUI user with an NVIDIA card: adopt it, get a first result | the pitch; "Start here" as one linear path with costs before the long steps (disk, hours for a build, the 44 GB set) and the other-GPU branch before the build, expected outputs and stop points; "Choose what to do next"; scope and support; a short privacy statement linking the page; license | any section a page owns |
| `CONTRIBUTING.md` (~900) | a contributor: change it safely | tools; layout table; why node dependencies are baked into the image (one paragraph) and the final-target naming by frontend and Sage mode; the gate command and strict mode; documentation changes (skill pointer, owner map); adding a pin; node and model set rules; settled decisions, one line each in TokenCrate's short form, only for choices an agent would otherwise reopen | the per-test-file list; CI narrative; rationale no one would question |
| `AGENTS.md` (~300) | agents | TokenCrate's `AGENTS.md` with LatentCrate's facts: the guiding principle verbatim ("Less is more", documentation as maintained surface area) and its one-source-of-truth paragraph (6.1, with the owner map as the only exceptions); the owner map; "no glossary, no changelog before the first tag"; the code rules; the documentation review gate pointing at the skill. Replaces today's file, whose repository map names pages and a checker the port deletes | policy text; anything the owner map already places |
| `SECURITY.md`, `THIRD_PARTY_NOTICES.md` | all | unchanged; the notices lose the tool-image rows | |
| `docs/configuration.md` (~1,000) | where is my data, which GPU works, how do I change a pin or remove it all | settings pointer and precedence; storage layout (the tree with container paths and flags, the symlink bind rule); GPU support (CDI as the only selector, driver floor, the arch list setting and how to find a card's compute capability, `keep-groups` needs `crun`, `HOST_MODEL_GID` on Docker); when a change takes effect (setting to command); pins and upgrading (the compatibility group that moves together, the stale Triton cache remedy); updating the checkout; removal (what `down` leaves, safe-to-delete table, image names) | settings tables; pin tables; flag syntax |
| `docs/models.md` (~600) | get the files a workflow needs | how a set works (manifest `destination` to `/models/<dir>` to a ComfyUI category through `extra_model_paths.yaml`); fetching (token, licenses printed, staging, never overwrites, stamps and `--verify`); manifest keys table; adding a set (`draft`, templates, `check`) | the set table; per-set tuning hints (a manifest `notes` field the downloader prints) |
| `docs/nodes.md` (~900) | add a node and make it load | source versus dependencies; after a Manager change (`up`); node sets (install, sync, refusals); local nodes (outside `/data`, read-only to Manager, no hot reload, unique names); what is captured (one sentence per rule with the file that holds it); SageAttention (three modes, the KJNodes path, the SA 2.2 API constraint) | commit hashes and pin rationale; Dockerfile package detail; image target lists |
| `docs/frontends.md` (~350) | test a frontend against a pinned backend | the modes and their trust boundary; Git mode; source mode (two phases, where the dist lands, why not the dev server); dist mode | hardening flag lists; flag syntax |
| `docs/privacy.md` (~700) | what can leave or be read | what runs where; defaults and their limits (bind address, the Manager policy file by path, the API-node and CSP coupling); strict mode; why no `internal` network (it would drop the published port); what is not covered | the copied `manager-config.ini`; helper paragraphs |
| `docs/troubleshooting.md` (~500) | fix the failing step | observed failures only, one `##` per message, refusal text verbatim, remedy, link to the owner page; asking for help | prerequisites; procedures owned elsewhere |
| `docs/validation.md` (~900) | what is checked and how to check it | status table; hosts; known gaps (the unverified list, NVENC-only smoke, Docker Desktop CDI, Docker with a GPU, WSL2); procedures (static gate, NVIDIA host baseline, feature checks, the WSL2 differences); recording a run | narrative records; a vocabulary section |

Deleted: `CHANGELOG.md`, index, getting-started, glossary, gpu-support,
cli (3.7 b), storage, upgrades, model-sets and third-party-nodes (their
survivors become `models.md` and `nodes.md`), templates, sageattention,
build-flow (the Dockerfile comment that cites it is fixed), ci,
trainers, validation-status, validation-history, arch-nvidia-validation,
wsl2-nvidia-validation, documentation-guidelines,
documentation-agent-checklist.

### 6.3 Glue knowledge the pages must keep

What a newcomer cannot read out of one file; the owner page in
brackets. The pruning pass checks this list before deleting a source
page. The "how" of each item stays in the file that implements it.

- Why node dependencies are baked in: third-party requirements are
  installed offline into an isolated tree loaded through `PYTHONPATH`
  (so `.pth` files are ignored) because nothing installs at container
  start and no compiler enters the runtime; a changed node means a
  rebuild, and `up` recreates the container after one. (CONTRIBUTING,
  nodes)
- Data layout: `/data`, `/models`, `/cache`, `/local` and their `COMFY_*`
  settings; `extra_model_paths.yaml` maps `/models` subdirectories to
  categories; local nodes sit outside `/data` so Manager cannot edit
  them; Manager's immediate installs land under the cache and are
  disposable; a symlink target outside the models directory needs an
  explicit bind at the same path. (configuration, nodes)
- Model sets: `destination` is relative to `/models` and must match a
  category; files stage in a hidden directory and publish atomically; a
  file with different content is never overwritten; the token reaches
  `huggingface.co` only. (models)
- Pins and variants: one `--sage` mode drives both the image target and
  the runtime flag so `doctor`, `up`, and `smoke-gpu` resolve the same
  image; the PyTorch pair, TorchCodec, and NPP move together; the arch
  list setting feeds both Sage and the custom-node builds.
  (configuration, nodes)
- Frontends: every mode ends in a `dist/` served through
  `--front-end-root`; a Git reference resolves to a commit that becomes
  a build argument; the dev server is not used because custom-node
  JavaScript loads differently. (frontends)
- Engine glue: CDI is the only GPU selector (`NVIDIA_VISIBLE_DEVICES=void`);
  `keep-groups` needs `crun`; Docker uses `HOST_MODEL_GID`;
  `COMPOSE_PROJECT_NAME` does not isolate images. (configuration)
- Sage: the KJNodes H3 patch uses SageAttention 2.2 APIs, so SA 3 is not
  a drop-in. (nodes)

## 7. Working method

Agents in layers, so that no session holds more context than its task
needs, and every decision outside section 3 goes to the maintainer.

### 7.1 Layers

- **The top-level orchestrator** owns this plan, the decision log
  (section 3 with its answers), and the state file
  `local/port-state.md` (`local/` is ignored by Git and survives a
  session, unlike a scratchpad): packages, their orchestrators' IDs and
  own state files, status, open hand-offs. It writes no code. It
  delegates by size: a package of several waves gets a package
  orchestrator, a package of one wave (A, D) a focused sub-agent
  directly, one package at a time per [section 8](#8-plan). It waits
  for the hand-off, spot-checks headline claims by reading the lines
  named, runs the strict gate once per integrated checkpoint (the end
  of a package; its result is the one that counts), checks between
  packages that `git status --short` lists only files owned by
  finished packages and that `git stash list` is empty (no session
  commits, so the tree is never clean), is the one voice to the
  maintainer, and evaluates each host run (reads
  `validate/latent-<timestamp>/`, writes the status table and the
  gaps). It never commits or stages.
- **A package orchestrator** owns one package's file set exclusively
  for its duration, reads only its section of this plan and the files
  the section names, and keeps its own state file under `local/`. It
  spawns focused sub-agents in waves with disjoint file ownership: the
  leaves other files import (`files`, `names`, `env`) are a wave of one
  agent before anything that imports them; a one-line edit in a file
  another agent owns is a hand-off, not an edit. It runs the package's
  own tests between waves and leaves the full gate to the top-level
  orchestrator; sub-agents never run the gate. A choice outside section 3 stops that thread and
  goes up as options ([7.2](#72-decision-protocol)); independent
  threads continue. Its hand-off is at most 300 words: files changed,
  behaviour established and the test that proves it, checks and their
  limits, unresolved choices, lines it needs in files it does not own.
- **A focused sub-agent** gets one brief: the plan paragraph, the
  TokenCrate module to copy from, the LatentCrate source it replaces,
  the file it owns, the test that proves it, the invariants. It reads
  those files itself; the brief pastes nothing. It returns a summary,
  not a file dump.
- **Reviewers** are separate agents with a lens and no stake. Before a
  package's code: an adversarial spar of its design, given the plan
  section and the code, not the orchestrator's conclusion. After: an
  adversarial review of `git diff` over the package's files. Each
  finding is reproduced, fixed, and reproduced again; a finding the
  reviewer and the fixer still dispute after the fix goes to the
  maintainer as a decision instead of another round. The documentation pass runs last by
  page ownership with the `documentation-guidelines` skill loaded. One
  independent cross-checker reads the full diff with the section list
  of this plan before the commit proposal.

### 7.2 Decision protocol

For a choice with materially different options:

1. The owning agent compares the smallest viable options against the
   repository's goal (pinned, reviewable, contained, less code), their
   cost, what each loses, and prototypes uncertain engine behaviour
   first.
2. An adversarial agent challenges the recommendation and searches for
   a lost guarantee, a needless option, or a cheaper mechanism.
3. The top-level orchestrator presents the maintainer with the format
   of section 3: current behaviour, the problem, the options with
   concrete costs and consequences, the recommendation, the remaining
   uncertainty. One related choice at a time, follow-ups on the answer,
   pushback allowed in both directions.
4. The answer is written into section 3; package E records it as a
   settled decision with reason and reconsideration condition. Nothing
   dependent is built before the answer.

Routine choices (a name, an error sentence, a test shape) are made by
the agent that owns the file, in the style of the module it changes.

### 7.3 Rules every agent gets

- Never run `git stash`, `git checkout`, `git reset`, `git add`,
  `git commit`, or anything that changes the working tree outside the
  files you own.
- Settled decisions (section 2 and the answers in section 3) are fixed;
  evidence against the code under one is a finding, evidence against
  the decision goes to the maintainer.
- A behaviour change arrives with its test in the gate and its line on
  the owner page, in the same change. Delete what a change makes unused.
- A change describes the new behaviour only: no comment, docstring,
  test, or page says what the code did before (TokenCrate's `AGENTS.md`
  rule, carried into LatentCrate's). A choice a reviewer might overturn
  gets its reason once, in one sentence, where the choice is made.
- Pre-empt the finding classes TokenCrate's reviews kept producing: the
  same validator written twice; one catalogue under several names;
  refusal wording that differs between `doctor` and the start; a
  compatibility shim for a file no release wrote; a feature with one
  user carrying hostile-input code; a rule restated on several pages;
  numbers in prose that drift; a test that pins presence only; CI on a
  Python newer than the floor.
- Repeat a new parallel-safety test when it guards a specific race
  (the lock, the atomic rename, the concurrent download), until one
  pass is evidence rather than luck; TokenCrate's parallel round had
  two load-only flakes.
- No session commits. The top-level orchestrator runs
  `LATENTCRATE_STATIC_STRICT=1 bash tests/static.sh`, proposes the
  message (subject, body, attribution line) and the files, and stops;
  every other agent runs only its package's tests.

## 8. Plan

Written for 3.6 (b). Delegation by size as 7.1 says (A and D are one
wave each); sizes are estimates (SUSPECTED).

### Cut 1: the image

**A. `pins.env`, the `comfy` service, the Dockerfile** (about -300
lines). `pins.env` from versions/edge.env under the 3.8 grammar with the
digests; the `comfy` service rewritten as 4.2 with `${VAR:?}`;
`compose.gpu.yaml`; the Dockerfile as 4.2, including the 3.1 outcome if
decided and a `--frontend-git` build for its measurement if not;
`services/comfy/rewrite-node-requirements.py` from the validation and
rewrite functions of scripts/snapshot-node-deps.py; `.dockerignore` as
an allowlist; `versions/current.env` deleted. The helper services,
`services/tools/`, `lib/`, and the Bash wrapper stay and get the minimum
edits to drive the new files (one profile, the copy step in place of
the snapshot), so that Run 1 can still run `nodes` and `templates`.
Verify: `config` renders on both engines in the VM; Run 1 on the host.

### Cut 2: the package

**B. Skeleton and host-only commands** (+2,700 lines). The 20-line
shim; `cli`, `env`, `names`, `files`, `engine`, `localhttp`, `doctor`,
`checkpins`; `.env.example` rewritten; `ruff.toml`; `tests/support.py`
and the module tests. Verify: unit tests; `pins`, `doctor`; `pins check`
online prints values equal to or newer than the seeded pins.

**C. Lifecycle, variants, models, nodes, templates** (+2,000, -3,800
lines). `variants`, `runtime`, `models/`, `nodes`, `templates`; delete
`lib/`, `services/tools/`, the helper services, and every script under
`scripts/`. Verify in the VM with rootless Podman: the 4.4 behaviours;
`models fetch` of the fixture set interrupted and resumed, a checksum
mismatch, `models status --verify`, `models check --remote`; `nodes
install` into a scratch data directory, an interruption, a second
`install` completing it, `sync`, `status`; `templates list` refusing
without the image; source mode's two phases on a fixture checkout.

**D. Gate and CI** (about -2,500 lines). `tests/static.sh`,
`tests/podman-compose.sh`, `tests/test_project_contracts.py` (the
documentation checks of check-project.py that survive move here before
it is deleted); the deletions of section 5; `static.yml` trimmed,
`build.yml` deleted, `requirements-dev.txt`, `dependabot.yml`. Verify:
the strict gate in a clean copy of the tree (section 5) on Python 3.11;
hadolint.

**E. Documentation** (about -18,500 words). Section 6, by page
ownership; section 2 and the answers of section 3 written into
`CONTRIBUTING.md` in TokenCrate's short form (decision, reason, link,
reconsider when); `AGENTS.md` rewritten from TokenCrate's as 6.2 says,
guiding principle and one-source-of-truth paragraph verbatim, owner map
LatentCrate's. Verify: the contracts test; the 6.3 list checked by hand;
the 6.1 grep.

**F. Run 2 on the host**, then the top-level orchestrator writes the
status table and the gaps, the cross-checker reads the full diff, the
gate runs, and the commit proposal follows.

## 9. Host validation

Mirrors TokenCrate's workflow (`CLAUDE.local.md` in that checkout). The
GPU host cannot pull; a clone at `/mnt/hostshare/latent-crate`
(`/home/panda/Virtual/share/latent-crate` on the host), a
`validate/sync-latent-from-vm.sh` that copies the tracked tree over the
clone keeping the host's `.git`, `.env`, `build`, `data`, `local`,
`reports` and writes `validate/latent-tree.sha256`, a
`validate-latent-host.sh` with steps `init doctor models build up smoke
nodes templates down`, each with a log under
`validate/latent-<timestamp>/` and a `summary.tsv`, and `host-todo.sh` as
one runnable script: the tree digest check and `bash bin/latentcrate
pins` first, no network and no Git, `set -Eeuo pipefail`, `HF_TOKEN`
unset, `exec </dev/null`, a `try()` per step, the stack left up at the
end. Never sync while the stack is up (Compose keeps a deleted directory
alive). Outputs never contain `HF_TOKEN`. Disk space on the share is
checked before the first build; nothing from September is assumed.

Run 1 (after cut 1, through the Bash wrapper): `doctor`; `build` for
`release`, `release-sage`, and `--frontend-git` (timed, for 3.1); `up
--detach`, `wait`, `smoke-gpu` for the first two; `nodes install
latent-nodepack`, `build`, `nodes status`; `down`. Run 2 (after cut 2,
through the package): the same, plus `models fetch minimax-h3-i2v`
(44 GB) killed at about a third and resumed, `models status --verify`,
an image-to-video run through the UI, `templates list`, source mode on
a frontend checkout, and the `up`/`down` state check. Docker with a GPU,
WSL2, and `--frontend-dist` stay in the known gaps unless a run covers
them. A finding goes to the page that owns the fact.

## 10. What might flow back to TokenCrate

Still true at 5e7b735 (CONFIRMED by a search for `{{.Image}}` and `image
inspect` in `tokencrate/`, no hit): the running-image assertion for
`smoke`; a healthcheck that proves the persistent state is writable; the
compute-capability coverage check as a pattern.

## Checks run for this document

Read-only. LatentCrate: `git rev-parse HEAD` 5588f7f, `git status
--short` shows the two untracked plan files, `git stash list` empty;
`wc -w` over the tracked Markdown; `grep -c` over `versions/*.env` and
`services/comfy/gpu-smoke.sh`; `compose.yaml` mounts;
`build-local-frontend.sh` phases. TokenCrate at 5e7b735: `git ls-files |
wc -l`, `wc -l` over the package, the `COMMANDS` table, `:?` in
`compose.yaml` (42), the image tags. Docker Hub's
`pytorch/pytorch:2.13.0-cuda13.0-cudnn9-runtime` manifest fetched once.
Three scoped sub-agents produced the inputs (TokenCrate decisions since
2026-09-25, the documentation pruning map, an adversarial review of the
2026-09-25 plan); one adversarial review of the first draft produced the
corrections in this version. Not run: any test, build, download, or
container.
