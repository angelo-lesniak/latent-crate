# Validation

What has been checked, on which host, and how to check it again. Behaviour
is stated as fact on the other pages; this page holds the evidence and its
limits. A row in `## Status` names the procedure, the host, the engine, the
date, and the result; a procedure without a row has no run recorded.

## Status

Each row names the commands run on the host as
[Recording a run](#recording-a-run) says, with the image built from the
checkout's `pins.env` and Dockerfile.

| Date | Host | Engine | Procedure | Result |
| --- | --- | --- | --- | --- |
| 2026-11-13 | GPU host | Podman 6.1.4 rootless, crun, podman-compose 1.6.0 | `bash tests/static.sh` strict, from a `.venv` built from `requirements-dev.txt` | pass; 320 tests |
| 2026-11-13 | GPU host | Podman 6.1.4 rootless, crun, podman-compose 1.6.0 | `init`, `doctor` | pass; `doctor` ends with 0 failures and 0 warnings |
| 2026-11-13 | GPU host | Podman 6.1.4 rootless, crun, podman-compose 1.6.0 | `build`, then `build --sage available` | pass; release 775 s with a cold engine cache (2026-11-12), 184 s warm; Sage 98 s on top; images 9.39 GB and 9.45 GB |
| 2026-11-13 | GPU host | Podman 6.1.4 rootless, crun, podman-compose 1.6.0 | `up --detach`, release and `--sage available` | pass; healthy in 65 s and 63 s |
| 2026-11-13 | GPU host | Podman 6.1.4 rootless, crun, podman-compose 1.6.0 | `smoke-gpu`, release and `--sage available` | pass; both reports end with `LatentCrate GPU verification passed.`, the Sage one holds `sageattention_result` |
| 2026-11-13 | GPU host | Podman 6.1.4 rootless, crun, podman-compose 1.6.0 | `models check --remote`; `models fetch` interrupted with Ctrl+C after 8 s and run again; `models status --verify` | pass; the second fetch resumed the `.part` file in 24 s and installed it, every checksum matched |
| 2026-11-13 | GPU host | Podman 6.1.4 rootless, crun, podman-compose 1.6.0 | `nodes install`, `nodes sync`, `nodes status` of `latent-nodepack`, then `up` | pass; 10 nodes ready, `up` healthy in 64 s |
| 2026-11-13 | GPU host | Podman 6.1.4 rootless, crun, podman-compose 1.6.0 | `templates list` through the sandboxed `engine run` | pass; 172 local-compatible templates |
| 2026-11-13 | GPU host | Podman 6.1.4 rootless, crun, podman-compose 1.6.0 | `up --frontend-source <checkout>` of the pinned frontend tag | pass; two runs of the Node image, the `dist` landed under `COMFY_CACHE_DIR/frontend-builds`, healthy in 106 s |
| 2026-11-13 | GPU host | Podman 6.1.4 rootless, crun, podman-compose 1.6.0 | `up --frontend-dist <dir>` with that `dist`, then `smoke-gpu` without the frontend setting | pass; healthy in 59 s; the smoke refused as [The running container uses another image](troubleshooting.md#the-running-container-uses-another-image) describes |
| 2026-11-13 | GPU host | Podman 6.1.4 rootless, crun, podman-compose 1.6.0 | `down` with no container of the project | pass; exit 0, no output |
| 2026-11-13 | GPU host | Podman 6.1.4 rootless, crun, podman-compose 1.6.0 | writes into the models directory by ComfyUI, Manager, or the node set during the scripted run | none |
| 2026-11-12 | GPU host | Podman 6.1.4 rootless, crun, podman-compose 1.6.0 | image generation through the UI with the `flux2-klein-9b` edit workflow on the Sage stack; a saved workflow reopened after `down` and `up` | pass (maintainer) |

## Hosts

| Host | Hardware and software |
| --- | --- |
| GPU host | Arch Linux, kernel 7.2.14-arch1-1; RTX 5090 (compute capability 12.0), NVIDIA driver 615.78.08, CUDA 13.4; Podman 6.1.4 rootless with crun, podman-compose 1.6.0 |

## Known gaps

- Docker Engine with a GPU.
- Docker Desktop's CDI path under WSL2.
- WSL2 as a whole: the expected platform, no run recorded.
- `build --frontend-git` with a reference other than the pinned tag.
- Dist or source mode combined with `--sage`: built in the gate's renders,
  not run on the host.
- A smoke on a card without the full encoder set: the smoke's NVENC step
  encodes one second with `h264_nvenc`, so it fails on a compute-only card
  even when CUDA passes.
- Whether Manager writes into `/models` over a long session; only the
  scripted run was observed. A read-only mount is decided after a run
  shows it.

## Procedures

### Static gate

[Tests](../CONTRIBUTING.md#tests) gives the gate command, its strict
variable, and which GPU checks a change needs.

### NVIDIA host baseline

Run on the GPU host, in this order:

```bash
bash tests/static.sh
bash bin/latentcrate doctor
bash bin/latentcrate up --detach
bash bin/latentcrate status
bash bin/latentcrate smoke-gpu
bash bin/latentcrate down
```

```text
bin/latentcrate smoke-gpu [--sage mode]
```

`smoke-gpu` runs the in-image GPU suite in the running container. It
proves CUDA, the Sage kernel when the mode is not `off`, the FFmpeg
encoders, TorchCodec decode, NVENC, and that the running container runs the
selected image; it refuses otherwise. The output streams to the terminal
and lands under `reports/gpu-<tag>-<timestamp>.log`.

A pass looks like this: `doctor` prints no `[fail]` line; `up` prints
`LatentCrate is healthy at <url>`; the smoke ends with
`LatentCrate GPU verification passed.`.

Persistence check: save a workflow in the UI, run `down` and `up`, and open
it again.

### Feature checks

- SageAttention: `bash bin/latentcrate up --sage available --detach` then
  `bash bin/latentcrate smoke-gpu --sage available`; the report holds a
  `== SageAttention ==` section with a `sageattention_result` line.
- Node sets: `bash bin/latentcrate nodes install latent-nodepack`, then
  `up` and `bash bin/latentcrate nodes status latent-nodepack`; every node
  reports ready.
- Model sets: interrupt `bash bin/latentcrate models fetch <set>`, run it
  again, then `bash bin/latentcrate models status --verify <set>`; every
  file is present and its checksum matches.
- Templates: `bash bin/latentcrate templates list` prints the templates of
  the built image.
- Git frontend: `bash bin/latentcrate build --frontend-git <https-url>
  <ref>` of a public reference builds and the UI serves it.
- Source frontend: `bash bin/latentcrate up --frontend-source <dir>` on a
  checkout builds and the UI serves it.
- Dist frontend: `bash bin/latentcrate up --frontend-dist <dir>` serves the
  prebuilt files.
- Docker Engine: the baseline with `CONTAINER_ENGINE=docker` in `.env` and
  CDI enabled in the daemon.

### WSL2 differences

- Run the Linux client inside the distribution; a Windows `.exe` client is
  refused.
- Keep the checkout and the storage directories on the Linux filesystem,
  not under `/mnt/c`.
- The Windows driver provides the GPU; no Linux driver in the distribution.
- Rootless Podman check: `podman info --format '{{.Host.Security.Rootless}}'`
  prints `true`.
- Docker Desktop's CDI support is one of the gaps listed in
  [Known gaps](#known-gaps).

## Recording a run

1. Create `.venv` from `requirements-dev.txt` as
   [Tools](../CONTRIBUTING.md#tools) says and run the strict gate from
   it.
2. Run `bash bin/latentcrate pins` and keep the output with the identity
   of the tree: the commit, or a checksum manifest of the working tree.
3. Keep each step's output.
4. Record the date, the host (OS, kernel, GPU, driver, CUDA version),
   the engine and Compose provider versions, the procedure, and the result
   as one row in `## Status`; add the host to `## Hosts` when it is new.
5. A finding goes to the page that owns the fact: a failure message to
   [troubleshooting](troubleshooting.md), a setting to
   [configuration](configuration.md), a feature to
   [nodes](nodes.md), [models](models.md), or [frontends](frontends.md).
6. Never paste `HF_TOKEN` or `.env`. Attach the smoke report from
   `reports/`.
