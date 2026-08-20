# Model sets

Model sets download the files used by selected official ComfyUI workflows.
Every file is pinned to a Hugging Face repository commit, expected size, and
SHA-256 checksum. Downloads resume when possible and become visible to ComfyUI
only after verification.

## Quick start

List the available sets:

```bash
bash bin/latentcrate models list
```

Download one or several sets:

```bash
bash bin/latentcrate models fetch flux2-klein-9b-distilled
bash bin/latentcrate models fetch krea2-t2i-int8 krea2-style-reference-int8
```

You can also fetch a set before the container starts:

```bash
bash bin/latentcrate up current \
  --model-set flux2-klein-9b-distilled \
  --detach
```

`--model-set` is repeatable. Downloads finish before the image build and
startup begin. Normal `up` does not download models unless you add this flag.

It is also safe to run `models fetch` while ComfyUI is open. Refresh the browser
tab after the command finishes. To verify files already on disk:

```bash
bash bin/latentcrate models status flux2-klein-9b-distilled
bash bin/latentcrate models status all
```

The status container runs with networking disabled. Its helper image may still
need to be built on first use. Checksum verification of large files can take a
little time.

## Included sets

| Set | Official workflow | Approximate download |
| --- | --- | ---: |
| `flux2-klein-9b-base` | [FLUX.2 text to image][flux-t2i] and [base image edit][flux-base-edit] | 18.5 GB |
| `flux2-klein-9b-distilled` | [FLUX.2 text to image][flux-t2i] and [distilled image edit][flux-distilled-edit] | 18.3 GB |
| `krea2-t2i-int8` | [Krea-2 text to image with INT8 ConvRot][krea-t2i] | 19.5 GB |
| `krea2-style-reference-int8` | [Krea-2 style reference with INT8 ConvRot][krea-style] | 19.4 GB |
| `minimax-h3-i2v` | [MiniMax H3 image to video][minimax-i2v] | 44.4 GB |
| `minimax-h3-r2v` | [MiniMax H3 reference to video][minimax-r2v] | 44.4 GB |

[flux-t2i]: https://github.com/Comfy-Org/workflow_templates/blob/3db6490611e6a16b84b09110e61a07264ce47cd3/templates/image_flux2_text_to_image_9b.json
[flux-base-edit]: https://github.com/Comfy-Org/workflow_templates/blob/3db6490611e6a16b84b09110e61a07264ce47cd3/templates/image_flux2_klein_image_edit_9b_base.json
[flux-distilled-edit]: https://github.com/Comfy-Org/workflow_templates/blob/3db6490611e6a16b84b09110e61a07264ce47cd3/templates/image_flux2_klein_image_edit_9b_distilled.json
[krea-t2i]: https://github.com/Comfy-Org/workflow_templates/blob/3db6490611e6a16b84b09110e61a07264ce47cd3/templates/image_krea2_turbo_t2i.json
[krea-style]: https://github.com/Comfy-Org/workflow_templates/blob/3db6490611e6a16b84b09110e61a07264ce47cd3/templates/image_krea2_turbo_int8_image_style_reference.json
[minimax-i2v]: https://github.com/Comfy-Org/workflow_templates/blob/3db6490611e6a16b84b09110e61a07264ce47cd3/templates/video_minimax_h3_i2v.json
[minimax-r2v]: https://github.com/Comfy-Org/workflow_templates/blob/3db6490611e6a16b84b09110e61a07264ce47cd3/templates/video_minimax_h3_r2v.json

Sets share files. Selecting several sets downloads a shared text encoder, VAE,
or model only once. `models fetch all` downloads every unique file, currently
about 115 GB when the model library starts empty.

Each FLUX set contains the shared files for both its text-to-image and edit
workflow. The downloader prints the matching pinned workflow links when it
runs.

Krea-2 uses the INT8 ConvRot model here. The BF16 model stack is about 31.8 GB
before runtime and activation memory, so it is not offered as a reliable 32 GB
RTX 5090 setup. In the official Krea-2 text-to-image workflow, select
`krea2_turbo_int8_convrot.safetensors` in the model loader after opening it.

The MiniMax H3 workflows also need KJNodes and SageAttention. The default image
has SageAttention, and `latent-nodepack` includes the pinned KJNodes source.

## Hugging Face access and licenses

The downloader prints every relevant model license before it changes files.
Review those licenses yourself; the repository's MIT license does not cover
model weights.

FLUX.2 Klein repositories are gated. Accept their license on Hugging Face,
create a read token, then add it to your uncommitted `.env`:

```dotenv
HF_TOKEN=hf_your_read_token
```

`latentcrate init` creates `.env` with mode `0600`. Run it again, or use
`chmod 600 .env`, if an existing file has wider permissions.

Krea-2 and MiniMax H3 currently download without a token. Hugging Face access
rules can change.

The wrapper removes `HF_TOKEN` from the environment passed to Compose and pipes
it through standard input to the short-lived download helper. The token is not
placed in command arguments, image layers, Compose configuration, or container
environment metadata. LatentCrate does not run `huggingface-cli login` or store
the token in its cache.

## Existing files, failures, and storage

An existing file with the expected size and checksum is reused. A file at the
same destination with different content is never overwritten: move it aside,
then run the command again.

Files download into a hidden staging directory beside their final destination.
The verified file is then published with an atomic no-overwrite operation. An
interrupted download can resume on the next run. A completed file that fails
its size or checksum check is discarded. Model downloads are sequential so
disk, network, and error handling stay predictable.

Atomic publication needs hard-link support. The helper tests each destination
before downloading and gives a direct error when a filesystem does not support
it. Interrupted transfers remain in `.latentcrate-downloads` directories. If
you abandon a transfer, wait until no model fetch is running, then remove the
matching hidden staging directory to reclaim its space; a later fetch starts
that file again.

The destination is `COMFY_MODELS_DIR`. If a model-category symlink points
outside that mount, the downloader rejects it by default. Follow the manual
mount guidance in [Storage layout](storage.md#model-symlinks-and-multiple-storage-devices),
add the same writable bind to the `model-set` helper, add a read-only bind to
`model-set-status`, and allow the container path with
`MODEL_SET_EXTRA_WRITE_ROOTS`. LatentCrate never discovers host symlink targets
or mounts them automatically.

Manifests live under `config/model-sets/`. They are data, not executable code,
and accept Hugging Face files only in this first version.

Contributors can start a new manifest from model metadata embedded in an
installed official workflow. See [Official template tools](templates.md). The
generated file is a draft: immutable revisions, licenses, sizes, checksums, and
destinations still require review.
