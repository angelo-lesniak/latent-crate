# Workflow sets

A workflow set is a manifest that lists ComfyUI workflow files and where
each came from. Every set appears in ComfyUI's workflow browser under
`latentcrate/<set>/`, a [private](#private-sets) one under
`latentcrate-local/<set>/`; `workflows check` names the
[model sets](models.md) and [node sets](nodes.md#node-sets) its
workflows need.

## How a set works

A manifest `config/workflow-sets/<set>.toml` lists the workflows in
`config/workflow-sets/workflows/<set>/`, subdirectories allowed. A
workflow is UI-format JSON, as ComfyUI saves it. A set's directory holds
exactly the files its manifest lists, and every entry of `workflows/`
belongs to a set; both `workflows` commands refuse anything else.

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
| `model <path>: no model set provides it` | The workflow loads a model file that no model set downloads. Add the file to a [private model set](models.md#private-sets) with `<path>` as its `destination`. A `<path>` without a directory is a bare file name, which matches that name in any model directory, such as `loras/<name>`. |
| `node pack <id> (<types>): <node-set>[, …]` | The workflow uses these node types from the custom-node pack `<id>`, and the named node sets install it. Nothing to do. |
| `node pack <id> (<types>): no custom-node set provides it` | No node set installs that pack. Add it to a [node set](nodes.md#node-sets). `check` reads only node sets, so a pack you keep in [local nodes](nodes.md#local-nodes) or installed through Manager shows up here too. |
| `node <type>: no pack id and not a core node at <COMFYUI_REF>` | The node is not built into the pinned ComfyUI, and the workflow does not say which pack it comes from. Find the pack, add it to a node set, and write its `cnr_id` or `aux_id` into the node's `properties`; that edit makes a `clone` a `modified-clone`. Or delete the node. |
| `node <type>: comfy-core but not a core node at <COMFYUI_REF>` | The workflow says the node is built into ComfyUI, but the pinned ComfyUI does not have it, usually because the workflow was saved on a newer ComfyUI. Delete the node, or wait for a `COMFYUI_REF` that has it. |

`check` exits `1` on a `no … provides it` or `not a core node` line.

A pack id is a node's `cnr_id`, else its `aux_id`. An `aux_id` matches
a node-set entry's GitHub `owner/repository`, a `cnr_id` its `name` or
repository name, case ignored, so a pack registered under another id
reads as not provided. `check` does not verify that a pack defines the
node type at its pinned commit. Core, frontend-only, muted, and bypassed
nodes print nothing; a subgraph prints the nodes inside it.

The core list includes the partner API nodes, so with
`COMFY_DISABLE_API_NODES=true` ([strict mode](privacy.md#strict-mode))
a workflow that uses them passes `check`, but ComfyUI lacks those nodes
at runtime. The list is read from ComfyUI's source, so it also holds
test nodes and nodes ComfyUI registers only under a condition; a
workflow that uses one of those passes `check` but may fail at runtime
([Validation](validation.md#status) records how many the host run did
not register).

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
   line [Check a set](#check-a-set) marks; for a shipped set, then run
   `bash tests/static.sh`.
   [Contributing](../CONTRIBUTING.md#node-model-and-workflow-sets) owns
   the review rules for a shipped set.
