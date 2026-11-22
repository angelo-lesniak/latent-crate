# Workflow sets

A workflow set is a manifest that lists ComfyUI workflow files and where
each came from. Every set appears in ComfyUI's workflow browser under
`latentcrate/<set>/`, a [private](#private-sets) one under
`latentcrate-local/<set>/`; `workflows check` names the
[model sets](models.md) and [node sets](nodes.md#node-sets) its
workflows need, and `workflows adapt` points its workflows at the model
files those sets download.

## How a set works

A manifest `config/workflow-sets/<set>.toml` lists the workflows in
`config/workflow-sets/workflows/<set>/`, subdirectories allowed. A
workflow is UI-format JSON, as ComfyUI saves it. A set's directory holds
exactly the files its manifest lists, and every entry of `workflows/`
belongs to a set; the `workflows` commands refuse anything else.

The container mounts every set read-only;
[Storage layout](configuration.md#storage-layout) owns the binds.
Saving into those folders fails with `Invalid filename`, and deleting
or renaming there fails with a server error. To change a set's
workflow, choose **Duplicate** in its menu and save the copy, which
lands at the top of the workflow folder.

## Check a set

```
bin/latentcrate workflows list
bin/latentcrate workflows check <set> [<set> ...] | all
bin/latentcrate workflows adapt <set> [<set> ...] | all
```

`check` works offline: it validates every workflow-set manifest and the
selected sets' JSON, then prints each model file and node pack a
workflow's active nodes load, with the model sets and node sets that
provide it. It compares against manifests, not the models directory or
the installed nodes; `models fetch` and `nodes install` take the sets it
names.

Each set starts with `<set>: <n> workflow(s)`; each line below it starts
with `<set>/<file>:`.

| Line | Meaning and what to do |
| --- | --- |
| `model <path>: <model-set>[, …]` | The workflow loads this model file, and the named model sets download it. `(local)` marks one of your private sets. Nothing to do. |
| `model <path>: re-select <destination> (<model-set>[, …])` | A model set downloads a file of the same name to `<destination>`, so ComfyUI does not find it at `<path>`. Run [`workflows adapt`](#adapt-a-set), or select the file in the loader node in ComfyUI. Several destinations, joined by `or`, mean the file name is not unique: select the right one by hand. |
| `model <path>: equivalent <destination> (<model-set>[, …]), <reason>` | No model set downloads this file, but a reviewed [equivalent](#equivalent-model-files) stands in for it; the line gives the reason. Run [`workflows adapt`](#adapt-a-set), or select `<destination>` in the loader node. |
| `model <path>: no model set provides it` | The workflow loads a model file that no model set downloads. Add the file to a [private model set](models.md#private-sets) with `<path>` as its `destination`. A `<path>` without a directory is a bare file name, which matches that name in any model directory, such as `loras/<name>`. |
| `node pack <id> (<types>): <node-set>[, …]` | The workflow uses these node types from the custom-node pack `<id>`, and the named node sets install it. Nothing to do. |
| `node pack <id> (<types>): no custom-node set provides it` | No node set installs that pack. Add it to a [node set](nodes.md#node-sets). `check` reads only node sets, so a pack you keep in [local nodes](nodes.md#local-nodes) or installed through Manager shows up here too. |
| `node <type>: no pack id and not a core node at <COMFYUI_REF>` | The node is not built into the pinned ComfyUI, and the workflow does not say which pack it comes from. Find the pack, add it to a node set, and write its `cnr_id` or `aux_id` into the node's `properties`; that edit makes a `clone` a `modified-clone`. Or delete the node. |
| `node <type>: comfy-core but not a core node at <COMFYUI_REF>` | The workflow says the node is built into ComfyUI, but the pinned ComfyUI does not have it, usually because the workflow was saved on a newer ComfyUI. Delete the node, or wait for a `COMFYUI_REF` that has it. |

`check` exits `1` on a `re-select`, `equivalent`, `no … provides it`, or
`not a core node` line.
A character in a workflow that does not print, such as an escape or a
tab, is printed as its escape sequence (`\x1b`, `\t`).

A pack id is a node's `cnr_id`, else its `aux_id`. An `aux_id` matches
a node-set entry's GitHub `owner/repository`, a `cnr_id` its `name` or
repository name, case ignored, so a pack registered under another id
reads as not provided. `check` does not verify that a pack defines the
node type at its pinned commit. Core, frontend-only, muted, and bypassed
nodes print nothing; a subgraph prints the nodes inside it.

The core list includes the partner API nodes, so with
`COMFY_DISABLE_API_NODES=true` ([strict mode](privacy.md#strict-mode))
a workflow that uses them passes `check`, but ComfyUI lacks those nodes
at runtime.

## Adapt a set

`workflows adapt` rewrites each workflow that has a `re-select` line
with one destination or an `equivalent` line. It changes only what
`check` reads, the active nodes and the subgraphs they use, and prints
one line per change and a summary:

- Each widget value that loads the file becomes the destination below
  its category directory, as ComfyUI's loader lists it:
  `qwen3vl_4b_fp8_scaled.safetensors` for
  `text_encoders/qwen3vl_4b_fp8_scaled.safetensors`. An equivalent
  prints its reason beside the change.
- Each `models` entry for the file gets the new name, the category as
  `directory`, and the pinned download URL as `url` (Hugging Face, or
  Civitai when the model set names `civitai_file`; otherwise no `url`);
  `hash`, `hash_type`, and `directory_invalid` are removed.
- A core `UNETLoader` whose model becomes an equivalent gets
  `weight_dtype` `default`, since the old setting suited the old file.
- The manifest entry records the change: a `clone` becomes a
  `modified-clone` with the note `model paths adapted to LatentCrate
  model sets`, a `modified-clone` gets `; model paths adapted` appended
  to its note once, and a `custom` entry stays unchanged. A comment
  after the edited value stays.

Every other byte of both files stays as it was, including line endings
and permissions. A shipped workflow set gets only files of shipped
model sets; a `re-select` to a private set's file is left for the
loader node and not printed. An equivalent is another file, so the workflow's output can
change slightly, for example with another precision.

A `re-select` with several destinations, or a destination that two
model sets declare differently, is printed as `not applied`. When
`adapt` cannot make an edit exactly, for example on a `note` written
as a multi-line TOML string, it leaves that workflow and its manifest entry unchanged, says
what to edit by hand, and exits `1`. A second run changes nothing.

### Equivalent model files

`config/model-equivalents.toml` lists the reviewed equivalents. Each
`[[equivalent]]` entry has these keys:

| Key | Meaning |
| --- | --- |
| `destination` | The `destination` of a file a shipped model set downloads |
| `names` | The file names, without a directory, that it stands in for; each name appears once in the table and is not the name of a shipped file |
| `reason` | One line that says why it stands in and cites its source at a commit, such as `same Krea 2 Turbo model, other precision: Comfy-Org/Krea-2 publishes all five at 588feaf` |

An equivalent is the same model in another precision or version, or a
replacement that its publisher declares drop-in or that the official
ComfyUI templates load. A file that needs other nodes or carries other
license terms is no equivalent. `check`, `adapt`, and the gate refuse a
table that breaks a rule of the key table.

## Private sets

The same layout under `local/workflow-sets/` (git-ignored; `init`
creates its `workflows/` directory) holds private sets with the same
keys and rules. `workflows list` prints them after the shipped sets,
marked `(local)`; a name a shipped set holds is refused. In a private
set, `source` may also be `https://civitai.com/models/<id>`, with or
without `?modelVersionId=<version>`, and `license.url` a Civitai model
page.

## Manifest keys

The manifest is TOML; `latentcrate/workflows.py` reads it and refuses
any other key and any key the entry's provenance does not take.

| Key | Required | Meaning |
| --- | --- | --- |
| `schema` | yes | `1` |
| `description` | yes | One non-empty line; `workflows list` prints it |
| `[[workflow]]` | one or more | One workflow file |
| `workflow.file` | yes | Path below `workflows/<set>/`, ending in `.json`; unique within the set |
| `workflow.provenance` | yes | `custom`: written for the set; `clone`: an unchanged copy of `source`; `modified-clone`: a changed copy of `source` |
| `workflow.source` | `clone`, `modified-clone` | The original's URL, in the forms of a model set's [`license.url`](models.md#manifest-keys) |
| `workflow.license` | `clone`, `modified-clone` | The copy's license: a table of `name` and `url`, with the rules of a model set's `[[license]]` |
| `workflow.note` | `modified-clone` | One non-empty line on what changed |

## Add a set

1. Save the workflow in ComfyUI and copy its file from
   `COMFY_DATA_DIR/user/default/workflows/` to
   `local/workflow-sets/workflows/<set>/`, or to
   `config/workflow-sets/workflows/<set>/` for a shipped set.
2. Write `<set>.toml` beside that `workflows/` directory, with one
   `[[workflow]]` entry and its provenance per file
   ([Manifest keys](#manifest-keys)).
3. Run `bash bin/latentcrate workflows check <set>` and resolve every
   line [Check a set](#check-a-set) marks, the `re-select` and
   `equivalent` lines with `workflows adapt <set>`; for a shipped set,
   then run `bash tests/static.sh`.
   [Contributing](../CONTRIBUTING.md#node-model-and-workflow-sets) owns
   the review rules for a shipped set.
