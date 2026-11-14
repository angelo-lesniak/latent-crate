# Model sets

A model set is the pinned list of Hugging Face or Civitai files one
ComfyUI workflow needs. `models fetch` downloads them into the models directory
(`COMFY_MODELS_DIR`, mounted at `/models`) and verifies each against the
manifest. [Configuration](configuration.md#storage-layout) owns where the
directory lives and the bind a symbolic link below it needs.

## How a set works

A manifest under `config/model-sets/` lists the files of one set: for
each, the Hugging Face repository, a commit, and the path in that
repository (or a [Civitai](#civitai-sources) model version), the
`destination` below `/models`, and the byte size and SHA-256 the
download must match. `destination` is relative to `/models`, and its
first directory must be one that
[`extra_model_paths.yaml`](../config/comfy/extra_model_paths.yaml) maps
to a ComfyUI model category, for example `diffusion_models/`,
`text_encoders/`, `vae/`, or `loras/`; a file placed elsewhere downloads
but no loader lists it. `bash bin/latentcrate models list` prints the
sets with their descriptions and is the source for what exists.
A file two selected sets both name downloads once; two sets that pin
different content at one destination are refused together.

## Fetch a set

```
bin/latentcrate models list
bin/latentcrate models fetch <set> [<set> ...] | all
bin/latentcrate models status [--verify] <set> [<set> ...] | all
bin/latentcrate models check [--remote] <set> [<set> ...] | all
bin/latentcrate models draft hf:<owner>/<repo>:<file> | civitai:<version>
```

`fetch` prints every license and pinned workflow link of the selected
sets before it changes anything, checks that the free space below the
models directory covers the bytes still to download, checks access to
every file, then downloads the files one after another. `up --model-set
<name>` runs the same fetch before starting ComfyUI and takes set names;
`all` belongs to the `models` commands.

The MiniMax H3 license grants rights only inside its "Applicable
Territory", which excludes the European Union, the United Kingdom, the
Republic of Korea and the United States, so read the linked license
before fetching a `minimax-h3-*` or `p-h3-image` set.

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
check after a file was swapped in place; for a file without a pinned
`sha256` the reference is its stamp, which the container can rewrite
with the file.

`check` validates the selected manifests offline. `check --remote`
compares each pinned file's size (and checksum, for LFS files) with the
Hugging Face record at the pinned revision, or a Civitai file's with the
record of its version, and requests every workflow and license link.

## Private sets

A manifest under `local/model-sets/` (git-ignored; `init` creates the
directory) is a private set with the same keys and rules, listed after
the shipped sets and marked `(local)` by `models list`; a name a shipped
set holds is refused. Its `[[file]]` entries may leave `size` and
`sha256` out. Such a file downloads to the length the server reports
(an interrupted download starts over, and the free-space check counts
no bytes for it), is published without an integrity check, and its
stamp records the checksum and size of that first copy; `status` prints
it as `unverified` while no readable stamp exists, `recorded` while it
matches the stamp, and `changed since recorded` when `--verify`, or a
size or modification time the stamp does not hold, finds a different
checksum. `check --remote` compares only the pins a file carries.

## Civitai sources

A `[[file]]` entry may name `civitai_version`, the number in
`https://civitai.com/api/download/models/<version>`, in place of
`repository`, `revision`, and `source`. The package builds that URL and
saves the version's primary file, or the one `civitai_file` names, at
`destination`; the file name Civitai offers is ignored. `civitai.red`
shares ids and accounts; requests go to `civitai.com`. `models draft
civitai:<version>` prints the entry with the size and checksum from
Civitai's record, lists the version's other files with their ids as
comments, and refuses a version without a single primary file, naming
its files. A file the creator limits to signed-in
accounts needs `CIVITAI_TOKEN` in `.env`;
[Privacy](privacy.md#what-runs-where) owns where the token goes.

Before adding a Civitai file, weigh its risks:

- `CIVITAI_TOKEN` is a second credential on disk, and it acts as your
  whole Civitai account.
- The uploader can delete or re-upload a version, so a pinned `sha256`
  can stop resolving; `fetch` then refuses instead of taking other bytes.
- The content and license are the uploader's, and nobody reviews them. A
  model page is edited in place, so only a private set may name
  `https://civitai.com/models/<id>` as its license; keep any file whose
  license is not clear in a [private set](#private-sets).

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
| `license.url` | yes | An HTTPS URL pinned to a 40-character commit on `huggingface.co`, `github.com`, or `cdn.jsdelivr.net`; a private set may also name `https://civitai.com/models/<id>` |
| `[[file]]` | one or more | One pinned Hugging Face file or Civitai model version |
| `file.repository` | Hugging Face files | `<owner>/<repository>` |
| `file.revision` | Hugging Face files | Full lowercase commit of the repository |
| `file.source` | Hugging Face files | Path of the file in the repository at that commit |
| `file.civitai_version` | Civitai files | Civitai model-version id, a positive integer; excludes the three keys above |
| `file.civitai_file` | no | With `civitai_version`: the id of a non-primary file of that version, a positive integer |
| `file.destination` | yes | Path below `/models`; unique within the set |
| `file.size` | shipped sets | Byte size, a positive integer |
| `file.sha256` | shipped sets | Lowercase hexadecimal SHA-256 of the file |

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
