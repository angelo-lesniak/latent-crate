# Validate on Arch Linux with NVIDIA

Use this guide when a change can affect the Linux container, NVIDIA runtime,
SageAttention, media tools, mounted data, frontends, or third-party nodes. Start
with the baseline, then run only the feature checks related to your change.

Completed results belong in [validation status](validation-status.md) and
[validation history](validation-history.md), not in this guide.

## Baseline

Choose the version profile you changed. The examples use `current`:

```bash
bash tests/static.sh
bash bin/latentcrate doctor current
bash bin/latentcrate config current
bash bin/latentcrate up current --detach
bash bin/latentcrate wait current
bash bin/latentcrate status current
bash bin/latentcrate smoke-gpu current
```

Then make these hands-on checks:

- Open ComfyUI at the address shown by `wait`.
- Run one small workflow that uses the GPU and saves an output.
- Check the startup log for failed imports, missing libraries, and permission
  errors.

Recreate the container without rebuilding its saved node dependencies:

```bash
bash bin/latentcrate down current
bash bin/latentcrate up current --use-saved-node-deps --detach
bash bin/latentcrate wait current
```

Confirm the workflow, input, output, and user data are still available.

Stop after a failure. Fix it or describe it clearly before treating the profile
as validated.

## Checks for the feature you changed

### Third-party nodes and Python packages

Run these checks after changing node capture, package resolution, native
libraries, or a bundled node set:

```bash
bash bin/latentcrate nodes status latent-nodepack current
bash bin/latentcrate up current --detach
```

- Confirm the dependency snapshot was refreshed and the image rebuilt.
- Confirm `python -m pip check` passes inside the running container.
- Import and exercise the affected nodes in a real workflow.
- For the included OpenCV policy, confirm `opencv-contrib-python` is the only
  installed OpenCV distribution that provides `cv2`.
- If local-only nodes changed, confirm they load from `COMFY_LOCAL_NODES_DIR`
  and do not modify their read-only source checkout.

### SageAttention, CUDA, FFmpeg, or TorchCodec

- Read the generated `smoke-gpu` report and confirm the expected GPU, compute
  capability, Torch, CUDA, Triton, SageAttention, and media results.
- Exercise the workflow affected by the change. A successful import alone does
  not prove that a CUDA kernel or codec works.
- For MiniMax H3 SageAttention work, confirm the memory-efficient Sage path is
  active and observe its peak VRAM use.
- If image selection changed, also build and test `--sage available`.

### Frontend modes

- Test the changed release, Git, local-source, or prebuilt-dist mode.
- Confirm the served frontend source and content digest match the running
  image.
- Open a workflow that uses third-party-node JavaScript.
- For a mutable Git reference or local source tree, change the frontend and
  confirm the next `up` serves the new build.

### Model sets

- Run `models status` for the set before and after `models fetch`.
- Test an interrupted download and confirm the next fetch resumes and publishes
  only the verified file.
- For a gated set, confirm the wrapper accepts `HF_TOKEN` through `.env` without
  placing it in container inspection output or logs.
- Open the linked official workflow, select the documented model variant when
  needed, and run a small result.

### Storage, permissions, privacy, or container engines

- Create data through ComfyUI and confirm the host UID, GID, and mounted model
  access are correct.
- Confirm the read-only root filesystem and restricted container permissions do
  not block the affected workflow.
- If privacy settings changed, test both the documented local-first default and
  the stricter offline option.
- If Docker, Podman, Compose, CDI, or lifecycle code changed, repeat the
  baseline with every engine affected by the patch.

## When a check fails

Keep the failing command and its complete output. Also include:

- the version profile and frontend mode;
- Docker or Podman and its Compose provider version;
- GPU model and NVIDIA driver version;
- the relevant generated file from `reports/`.

The full host inventory is usually unnecessary. Add more system information
only when the failure appears specific to the driver, engine, storage, or Linux
distribution.

## Recording a successful validation

Update [validation status](validation-status.md) when the result changes what
the project can claim as tested. Put dated evidence in
[validation history](validation-history.md). Record the profile, image digest,
engine, GPU, driver, commands run, and any checks that were intentionally not
applicable.
