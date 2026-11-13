# Model sets

A model set is the pinned list of Hugging Face files one ComfyUI
workflow needs. `models fetch` downloads them into the models directory
(`COMFY_MODELS_DIR`, mounted at `/models`) and verifies each against the
manifest. [Configuration](configuration.md#storage-layout) owns where the
directory lives and the bind a symbolic link below it needs.

## How a set works

A manifest under `config/model-sets/` lists the files of one set: for
each, the Hugging Face repository, a commit, the path in that repository,
the `destination` below `/models`, and the byte size and SHA-256 the
download must match. `destination` is relative to `/models`, and its
first directory must be one that
[`extra_model_paths.yaml`](../config/comfy/extra_model_paths.yaml) maps
to a ComfyUI model category, for example `diffusion_models/`,
`text_encoders/`, `vae/`, or `loras/`; a file placed elsewhere downloads
but no loader lists it. `bash bin/latentcrate models list` prints the
shipped sets with their descriptions and is the source for what exists.
A file two selected sets both name downloads once; two sets that pin
different content at one destination are refused together.

## Fetch a set

```
bin/latentcrate models list
bin/latentcrate models fetch <set> [<set> ...] | all
bin/latentcrate models status [--verify] <set> [<set> ...] | all
bin/latentcrate models check [--remote] <set> [<set> ...] | all
bin/latentcrate models draft hf:<owner>/<repo>:<file>
```

`fetch` prints every license and pinned workflow link of the selected
sets before it changes anything, checks that the free space below the
models directory covers the bytes still to download, checks access to
every file, then downloads the files one after another. `up --model-set
<name>` runs the same fetch before starting ComfyUI and takes set names;
`all` belongs to the `models` commands.

A gated repository needs `HF_TOKEN` in `.env`;
[Privacy](privacy.md#what-runs-where) owns where the token goes.

Each file streams into `.latentcrate-downloads` beside its destination
and is published by one rename after its size and checksum match, so a
destination is either absent or complete. An interrupted download
resumes from the staged bytes on the next `fetch`. A present file whose
size or checksum differs from the manifest is never overwritten: `fetch`
stops and names it. A lock file in the models directory lets one `fetch`
run at a time per models directory. The common refusals are sections
of [troubleshooting](troubleshooting.md).

A verified file gets a stamp beside it, `<file>.latentcrate-verified`,
recording its SHA-256, size, and modification time. `status` and
`fetch` trust the stamp while the size and time match, so a library is
hashed once; `status --verify` hashes every file again, which is the
check after a file was swapped in place.

`check` validates the selected manifests offline. `check --remote`
compares each pinned file's size (and checksum, for LFS files) with the
Hugging Face record at the pinned revision and requests every workflow
and license link.

## Manifest keys

The manifest is TOML; `latentcrate/models/manifest.py` reads it and
refuses any other key.

| Key | Required | Meaning |
| --- | --- | --- |
| `schema` | yes | `1` |
| `description` | yes | One non-empty line; `models list` prints it |
| `workflow_urls` | yes | Unique commit-pinned `Comfy-Org/workflow_templates` blob URLs |
| `[[license]]` | one or more | A model license the user accepts before downloading |
| `license.name` | yes | Shown by `fetch` |
| `license.url` | yes | An HTTPS URL pinned to a 40-character commit on `huggingface.co`, `github.com`, or `cdn.jsdelivr.net` |
| `[[file]]` | one or more | One pinned Hugging Face file |
| `file.repository` | yes | `<owner>/<repository>` |
| `file.revision` | yes | Full lowercase commit of the repository |
| `file.source` | yes | Path of the file in the repository at that commit |
| `file.destination` | yes | Path below `/models`; unique within the set |
| `file.size` | yes | Byte size, a positive integer |
| `file.sha256` | yes | Lowercase hexadecimal SHA-256 of the file |

## Add a set

```
bin/latentcrate templates list
bin/latentcrate templates create-model-set <template> [--name name]
```

Both run the template tool inside the release-frontend image for the
Sage mode set in `.env`, so the template package they read is the one
the image serves; a missing image is refused and `build` named.
`templates list` prints the official templates marked open source,
active, and for local use.

1. Build the image, then run `bash bin/latentcrate templates list` and
   pick a template.
2. Run `bash bin/latentcrate templates create-model-set <template>`. It
   writes `build/model-set-drafts/<name>.toml` with a `TODO` marker at
   every value it could not derive; `fetch` rejects a draft until every
   `TODO` is resolved, and an existing draft is never overwritten.
3. For each file, run `bash bin/latentcrate models draft
   hf:<owner>/<repo>:<file>`; it prints one `[[file]]` entry with the
   commit of `main`, the size, and the checksum, leaving `destination`
   for you to prefix with the model directory.
4. Complete the draft (licenses, the workflow commit, destinations) and
   move it to `config/model-sets/`.
5. Run `bash bin/latentcrate models check --remote <name>`, then `bash
   tests/static.sh`. [Contributing](../CONTRIBUTING.md#node-sets-and-model-sets)
   owns the review rules for a shipped set.
