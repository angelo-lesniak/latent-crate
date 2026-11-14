# Validation

What has been checked, on which host, and how to check it again. Behaviour
is stated as fact on the other pages; this page holds the evidence and its
limits. A row in `## Status` names the date, the host, the procedure, and
the result, and `## Hosts` the host's engine; a procedure without a row has
no run recorded.

## Status

Each row names the commands run on the host as
[Recording a run](#recording-a-run) says, with the image built from the
checkout's `pins.env` and Dockerfile.

| Date | Host | Procedure | Result |
| --- | --- | --- | --- |
| 2026-11-13 | GPU host | `bash tests/static.sh` strict, from a `.venv` built from `requirements-dev.txt` | pass; 326 tests |
| 2026-11-13 | GPU host | `init`, `doctor` | pass; `doctor` ends with 0 failures and 0 warnings |
| 2026-11-13 | GPU host | `build`, then `build --sage available` (ComfyUI v0.45.0, PyTorch 2.14.1 with CUDA 13.0) | pass; release 1983 s with cold base images, Sage 446 s on top; images 10.2 GB each |
| 2026-11-13 | GPU host | `up --detach`, release and `--sage available` | pass; healthy in 65 s and 67 s |
| 2026-11-13 | GPU host | `smoke-gpu`, release and `--sage available` | pass; both reports end with `LatentCrate GPU verification passed.`, the Sage one holds `sageattention_result` |
| 2026-11-13 | GPU host | `models check --remote`; `models fetch minimax-h3-i2v-turbo-4step` interrupted with Ctrl+C after 8 s and run again; `models status --verify` | pass; the second fetch resumed and installed the file in 26 s, every checksum matched |
| 2026-11-13 | GPU host | `nodes sync latent-nodepack` over the library of the previous run, `up`, `smoke-gpu` | pass; 10 nodes ready, `ComfyUI-WanVideoWrapper` among them; `up` healthy in 1477 s with the full rebuild; the smoke ends with `LatentCrate GPU verification passed.` |
| 2026-11-13 | GPU host | `models fetch krea2-turbo-4step-lora minimax-h3-image-int8 minimax-h3-image-pdmd-2step`, `models status --verify`, `up` | pass; 59.0 GB in 6 files installed, every file ready; `doctor` 0 failures and 0 warnings, `up` healthy |
| 2026-11-13 | GPU host | `models list`, `models fetch`, `models status --verify` of private sets with Civitai files | pass; 6 files, 0.84 GB, every file ready |
| 2026-11-13 | GPU host | `templates list` through the sandboxed `engine run` | pass; 216 local-compatible templates from comfyui-workflow-templates 0.11.105 |
| 2026-11-13 | GPU host | `up --frontend-source <checkout>` of the pinned frontend tag | pass; two runs of the Node image, the `dist` landed under `COMFY_CACHE_DIR/frontend-builds`, healthy in 107 s |
| 2026-11-13 | GPU host | `up --frontend-dist <dir>` with that `dist`, then `smoke-gpu` without the frontend setting | pass; healthy in 65 s; the smoke refused as [The running container uses another image](troubleshooting.md#the-running-container-uses-another-image) describes |
| 2026-11-13 | GPU host | `down` with no container of the project | pass; exit 0, no output |
| 2026-11-13 | GPU host | writes into the models directory by ComfyUI, Manager, or the node set during the scripted run | none |
| 2026-11-13 | GPU host | attention A/B: the official MiniMax H3 i2v template (124 frames, 1344×768, one seed) through the API per backend; wall seconds and device-wide peak GPU memory | SDPA 150 s; `--use-ck-attention` 93 s; Model Sparse Attention 83 s; KJNodes patch under `--sage available` 77 s; `--sage global` with the patch 76 s; `--sage global` alone 71 s; peak 31.1–31.3 GiB of 31.8 GiB in every run; 124 frames each; visually (maintainer): block-sparse noticeably worse, the rest indistinguishable |
| 2026-11-14 | GPU host | UI generation: template `image_minimax_h3_image_t2i`; `image_minimax_h3_image_edit` with a reference image; `image_krea2_turbo_t2i_int8` with the `krea2_turbo_4step_rank_64_lora_comfyui` LoRA at 4 steps; `image_minimax_h3_image_t2i` with the `minimax_h3_pdmd_2nfe_comfyui` LoRA at 2 steps | pass (maintainer) |

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
- Workflow persistence across `down` and `up` on the pinned image.
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
4. Record the date, the host, the procedure, and the result as one row
   in `## Status`; add the host (OS, kernel, GPU, driver, CUDA version,
   engine and Compose provider versions) to `## Hosts` when it is new.
5. A finding goes to the page that owns the fact: a failure message to
   [troubleshooting](troubleshooting.md), a setting to
   [configuration](configuration.md), a feature to
   [nodes](nodes.md), [models](models.md), or [frontends](frontends.md).
6. Never paste `HF_TOKEN`, `CIVITAI_TOKEN`, or `.env`. Attach the smoke report from
   `reports/`.
