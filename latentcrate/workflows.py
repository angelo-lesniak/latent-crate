"""Read workflow-set manifests and check what their workflows need.

A set is `<name>.toml` in `config/workflow-sets` (shipped) or
`local/workflow-sets` (private), with its UI-format workflow JSON below
`workflows/<name>/` beside the manifest. Only each `workflows` directory is
mounted into ComfyUI's user workflow directory: ComfyUI lists every
non-hidden file below it (`/userdata?recurse=true`, app/user_manager.py at
COMFYUI_REF) and the pinned frontend shows each one in its workflow browser
(src/utils/syncUtil.ts at COMFYUI_FRONTEND_REF), so a manifest there would
show up as a workflow.

The CLI runs this module in-process on the host: ``workflows list`` reads
:func:`catalog` and ``workflows check`` calls :func:`check`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from . import LatentCrateError
from .files import read_utf8
from .models.manifest import CIVITAI_MODEL_URL_RE, IMMUTABLE_URL_RE, License, ModelSet, read_license, require_keys
from .names import SET_NAME_RE, read_toml, relative_path
from .names import description as read_description
from .nodes import Node, NodeSet

# The directory beside the manifests that compose.yaml mounts.
WORKFLOWS_DIR = "workflows"
# Kept in the mounted directory for Git; ComfyUI does not list hidden files.
KEEP_NAME = ".gitkeep"
PROVENANCE_KEYS = {
    "custom": {"file", "provenance"},
    "clone": {"file", "provenance", "source", "license"},
    "modified-clone": {"file", "provenance", "source", "license", "note"},
}
CIVITAI_VERSION_URL_RE = re.compile(r"^https://civitai\.com/models/[1-9][0-9]*\?modelVersionId=[1-9][0-9]*$")
# The node types the pinned frontend defines itself (SYSTEM_NODE_DEFS in
# src/stores/nodeDefStore.ts at COMFYUI_FRONTEND_REF), which no node pack
# provides; a subgraph instance's type is its definition's id.
FRONTEND_NODE_TYPES = frozenset({"Note", "MarkdownNote", "PrimitiveNode", "Reroute"})
# The node types ComfyUI registers itself at COMFYUI_COMMIT, written by
# scripts/core-nodes.py: a node without a pack id resolves when it is one.
CORE_NODES_FILE = Path("comfy") / "core-nodes.txt"
# Muted (NEVER) and bypassed (BYPASS) nodes do not run: LGraphEventMode in
# src/lib/litegraph/src/types/globalEnums.ts at COMFYUI_FRONTEND_REF.
INACTIVE_MODES = (2, 4)
# Free text, whose widget values are never model names.
NOTE_TYPES = frozenset({"Note", "MarkdownNote"})
CORE_PACK = "comfy-core"
# Packs whose node types carry their name, for nodes saved without a pack
# id: rgthree-comfy suffixes every type with " (rgthree)" (get_name in
# py/constants.py, addRgthree in src_web/comfyui/constants.ts), and
# VideoHelperSuite prefixes its types with "VHS_" (NODE_CLASS_MAPPINGS in
# videohelpersuite/nodes.py), both at the commits latent-nodepack pins.
NAMED_PACKS = ((" (rgthree)", "", "rgthree-comfy"), ("", "VHS_", "comfyui-videohelpersuite"))
# ComfyUI's supported_pt_extensions (folder_paths.py at COMFYUI_REF) and
# GGUF, which quantized-model loader packs read.
MODEL_SUFFIXES = (".ckpt", ".pt", ".pt2", ".bin", ".pth", ".safetensors", ".pkl", ".sft", ".gguf")


@dataclass(frozen=True)
class Workflow:
    file: str
    provenance: str
    source: str | None = None
    license: License | None = None
    note: str | None = None


@dataclass(frozen=True)
class WorkflowSet:
    name: str
    description: str
    # The set's mounted JSON directory.
    directory: Path
    workflows: tuple[Workflow, ...]
    private: bool = False


@dataclass
class Needs:
    """What one workflow loads: model files as (directory, name), the
    directory empty for a bare widget value; node packs by their
    ("cnr_id" | "aux_id", id) with the node types they provide; and the
    node types that are no core node although they name no pack or name
    comfy-core, each with how its line reads."""

    models: set[tuple[str, str]] = field(default_factory=set)
    packs: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    unknown: dict[str, str] = field(default_factory=dict)


# --- Manifests ---------------------------------------------------------------


def read_manifest(path: Path, *, private: bool = False) -> WorkflowSet:
    """The set a manifest declares; its directory must hold exactly the listed workflows."""
    name = path.stem
    if not SET_NAME_RE.fullmatch(name) or name == "all":
        raise LatentCrateError(f"unsafe workflow-set filename: {path.name}")
    document = read_toml(path, {"schema", "description", "workflow"}, name)
    description = read_description(document, name)
    raw_workflows = document.get("workflow")
    if not isinstance(raw_workflows, list) or not raw_workflows:
        raise LatentCrateError(f"{name}: at least one [[workflow]] entry is required")
    workflows: list[Workflow] = []
    for index, raw in enumerate(raw_workflows, start=1):
        workflow = read_workflow(raw, f"{name}: workflow {index}", private)
        if any(workflow.file == seen.file for seen in workflows):
            raise LatentCrateError(f"{name}: duplicate workflow: {workflow.file}")
        workflows.append(workflow)
    directory = path.parent / WORKFLOWS_DIR / name
    present = files_below(directory, name)
    unlisted = sorted(present - {workflow.file for workflow in workflows})
    if unlisted:
        raise LatentCrateError(f"{name}: {directory} holds files the manifest does not list: {', '.join(unlisted)}")
    missing = [workflow.file for workflow in workflows if workflow.file not in present]
    if missing:
        raise LatentCrateError(f"{name}: listed workflows are missing from {directory}: {', '.join(missing)}")
    return WorkflowSet(name, description, directory, tuple(workflows), private)


def read_workflow(raw: object, context: str, private: bool) -> Workflow:
    """One [[workflow]] entry: a JSON file below the set's directory and its
    provenance; a clone cites its commit-pinned source and its license, a
    modified clone also one line on what changed."""
    if not isinstance(raw, dict):
        raise LatentCrateError(f"{context} must be a table")
    require_keys(raw, set().union(*PROVENANCE_KEYS.values()), context)
    file = relative_path(raw.get("file"), "file", context)
    if not file.endswith(".json"):
        raise LatentCrateError(f"{context}: file must name a .json workflow: {file}")
    provenance = raw.get("provenance")
    expected = PROVENANCE_KEYS.get(provenance) if isinstance(provenance, str) else None
    if expected is None:
        raise LatentCrateError(f"{context}: provenance must be custom, clone, or modified-clone")
    if set(raw) != expected:
        raise LatentCrateError(f"{context}: a {provenance} workflow takes exactly {', '.join(sorted(expected))}")
    if provenance == "custom":
        return Workflow(file, provenance)
    source = raw["source"]
    civitai = (
        private
        and isinstance(source, str)
        and any(pattern.fullmatch(source) for pattern in (CIVITAI_MODEL_URL_RE, CIVITAI_VERSION_URL_RE))
    )
    if not civitai and not (isinstance(source, str) and IMMUTABLE_URL_RE.fullmatch(source)):
        raise LatentCrateError(f"{context}: source must be an immutable approved HTTPS URL")
    workflow_license = read_license(raw["license"], f"{context} license", private)
    note = raw.get("note")
    if provenance == "modified-clone" and (not isinstance(note, str) or not note.strip() or "\n" in note.strip()):
        raise LatentCrateError(f"{context}: note must be one non-empty line")
    return Workflow(file, provenance, source, workflow_license, note.strip() if isinstance(note, str) else None)


def files_below(directory: Path, name: str) -> set[str]:
    """Every file below a set's directory, as the manifest spells it. A
    symbolic link or special file is refused here, in `workflows list` and
    `check`; `up` mounts the directory without reading it."""
    if directory.is_symlink() or not directory.is_dir():
        raise LatentCrateError(f"{name}: the workflow directory is missing or not a directory: {directory}")
    found: set[str] = set()

    # os.walk skips a directory it cannot read unless told otherwise, which
    # would hide the files in it from the comparison.
    def unreadable(error: OSError) -> None:
        raise error

    for parent, directory_names, file_names in os.walk(directory, onerror=unreadable):
        for entry in (Path(parent) / item for item in directory_names + file_names):
            if entry.is_symlink() or not (entry.is_dir() or entry.is_file()):
                raise LatentCrateError(f"{name}: only plain files and directories may sit in {directory}: {entry}")
        found.update((Path(parent) / item).relative_to(directory).as_posix() for item in file_names)
    return found


def load_sets(directory: Path, *, private: bool = False) -> dict[str, WorkflowSet]:
    """The sets declared in one manifest directory (absent is empty), in
    set-name order; every entry of the mounted directory beside them belongs
    to one of them."""
    sets: dict[str, WorkflowSet] = {}
    for path in sorted(directory.glob("*.toml"), key=lambda item: item.stem):
        workflow_set = read_manifest(path, private=private)
        sets[workflow_set.name] = workflow_set
    mounted = directory / WORKFLOWS_DIR
    if mounted.is_dir():
        for entry in sorted(mounted.iterdir()):
            keep = entry.name == KEEP_NAME and entry.is_file() and not entry.is_symlink()
            if not keep and entry.name not in sets:
                raise LatentCrateError(
                    f"{entry} belongs to no workflow set; only a set's directory, named like its manifest, "
                    f"or a plain {KEEP_NAME} file may sit in {mounted}"
                )
    return sets


def catalog(config_dir: Path, local_dir: Path | None = None) -> dict[str, WorkflowSet]:
    """Every shipped workflow set under config_dir/workflow-sets (none is
    fine), then the private sets under local_dir, by name; a name in both is
    refused."""
    shipped_dir = config_dir / "workflow-sets"
    sets = load_sets(shipped_dir)
    if local_dir is None:
        return sets
    for name, workflow_set in load_sets(local_dir, private=True).items():
        if name in sets:
            raise LatentCrateError(
                f"workflow set {name} is declared twice: {shipped_dir / name}.toml and {local_dir / name}.toml"
            )
        sets[name] = workflow_set
    return sets


# --- Workflow contents -------------------------------------------------------


def read_workflow_json(path: Path, context: str) -> dict:
    try:
        document = json.loads(read_utf8(path))
    # ValueError covers malformed JSON and an integer too long to convert;
    # RecursionError, nesting deeper than the parser's stack.
    except (ValueError, RecursionError) as error:
        raise LatentCrateError(f"{context}: not valid JSON ({error})") from error
    if not isinstance(document, dict) or not isinstance(document.get("nodes"), list):
        raise LatentCrateError(f"{context}: not a UI-format workflow (no nodes list)")
    return document


def subgraph_definitions(document: dict, context: str) -> dict[str, dict]:
    """Every subgraph definition by id: the frontend keeps them all in the
    workflow's own `definitions.subgraphs`, nested subgraphs included."""
    definitions = document.get("definitions") or {}
    subgraphs = definitions.get("subgraphs", []) if isinstance(definitions, dict) else None
    if not isinstance(subgraphs, list):
        raise LatentCrateError(f"{context}: definitions.subgraphs must be a list")
    found: dict[str, dict] = {}
    for subgraph in subgraphs:
        if not isinstance(subgraph, dict) or not isinstance(subgraph.get("id"), str):
            raise LatentCrateError(f"{context}: every subgraph definition needs an id")
        if not isinstance(subgraph.get("nodes"), list):
            raise LatentCrateError(f"{context}: subgraph {subgraph['id']} has no nodes list")
        found[subgraph["id"]] = subgraph
    return found


def model_name(value: str, context: str) -> str:
    """A model path as ComfyUI resolves it; one that would print as more
    than one plain line is refused."""
    if not value.isprintable():
        raise LatentCrateError(f"{context}: model name {value!r} has a character that does not print")
    return value.replace("\\", "/")


def declared_models(value: object, context: str) -> set[tuple[str, str]]:
    """A `models` list (a node's properties or a graph's), as official
    templates write it: each entry a name and usually its directory."""
    if value is None:
        return set()
    if not isinstance(value, list) or not all(isinstance(entry, dict) for entry in value):
        raise LatentCrateError(f"{context}: models must be a list of tables")
    found = set()
    for entry in value:
        name, directory = entry.get("name"), entry.get("directory", "")
        if not isinstance(name, str) or not name or not isinstance(directory, str):
            raise LatentCrateError(f"{context}: a models entry needs a name and a directory string")
        found.add((model_name(directory, context).strip("/"), model_name(name, context)))
    return found


def widget_models(values: object, context: str) -> set[str]:
    """The widget values that name a model file, relative to its category,
    at any depth. A table is a row of a multi-model loader (rgthree's Power
    Lora Loader: `{"on": true, "lora": "x.safetensors", ...}`) and counts
    unless switched off; a string that holds JSON (a LoRA list some packs
    keep in one widget) is read as the value it encodes."""
    found: set[str] = set()
    pending = [values]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if value.get("on") is not False:
                pending += value.values()
        elif isinstance(value, list):
            pending += value
        elif isinstance(value, str) and value.lstrip().startswith(("{", "[")) and (encoded := json_value(value)):
            pending.append(encoded)
        elif isinstance(value, str) and "\n" not in value and value.lower().endswith(MODEL_SUFFIXES):
            found.add(model_name(value, context))
    return found


def json_value(text: str) -> object:
    """The value a widget string encodes, or None for text that only looks like JSON."""
    try:
        return json.loads(text)
    # Text that does not parse is an ordinary string, which the caller reads as one.
    except (ValueError, RecursionError):
        return None


def needs(document: dict, context: str, core_nodes: frozenset[str]) -> Needs:
    """The model files and node packs a workflow loads: its running nodes,
    and the subgraph definitions a running node instantiates."""
    definitions = subgraph_definitions(document, context)
    found = Needs()
    bare: set[str] = set()
    pending, entered = [document], set()
    while pending:
        graph = pending.pop()
        found.models |= declared_models(graph.get("models"), context)
        for node in graph["nodes"]:
            if not isinstance(node, dict) or not isinstance(node.get("type"), str) or not node["type"]:
                raise LatentCrateError(f"{context}: every node needs a type")
            kind = node["type"]
            if not kind.isprintable():
                raise LatentCrateError(f"{context}: node type {kind!r} has a character that does not print")
            # A hand-edited file may write null; the frontend saves a table.
            properties = node.get("properties") if node.get("properties") is not None else {}
            if not isinstance(properties, dict):
                raise LatentCrateError(f"{context}: node {kind} has properties that are not a table")
            if node.get("mode") in INACTIVE_MODES:
                continue
            names = set() if kind in NOTE_TYPES else widget_models(node.get("widgets_values"), context)
            # Templates list alternatives too; an entry counts once a widget selects it.
            selected = {entry for entry in declared_models(properties.get("models"), context) if entry[1] in names}
            found.models |= selected
            bare |= names - {name for _, name in selected}
            if kind in definitions:
                if kind not in entered:
                    entered.add(kind)
                    pending.append(definitions[kind])
                continue
            if kind in FRONTEND_NODE_TYPES:
                continue
            # The frontend's own order (getCnrIdFromProperties): a non-empty cnr_id, then aux_id.
            cnr_id, aux_id = properties.get("cnr_id"), properties.get("aux_id")
            for pack_id in (cnr_id, aux_id):
                if isinstance(pack_id, str) and not pack_id.isprintable():
                    raise LatentCrateError(f"{context}: node {kind} has a pack id {pack_id!r} that does not print")
            if cnr_id == CORE_PACK:
                # Saved on a newer ComfyUI, with a node the pinned one lacks.
                if kind not in core_nodes:
                    found.unknown[kind] = "comfy-core but"
                continue
            if isinstance(cnr_id, str) and cnr_id:
                found.packs.setdefault(("cnr_id", cnr_id), set()).add(kind)
            elif isinstance(aux_id, str) and aux_id:
                found.packs.setdefault(("aux_id", aux_id), set()).add(kind)
            elif kind in core_nodes:
                continue
            elif named := [
                pack for suffix, prefix, pack in NAMED_PACKS if kind.endswith(suffix) and kind.startswith(prefix)
            ]:
                found.packs.setdefault(("cnr_id", named[0]), set()).add(kind)
            else:
                found.unknown[kind] = "no pack id and"
    found.models |= {("", name) for name in bare - {name for _, name in found.models}}
    return found


def provides(node: Node, key: str, pack: str) -> bool:
    """Whether a node-set entry is the pack a workflow names. An aux_id is
    the GitHub owner/repository. A cnr_id is the registry id, which the
    pack's own pyproject.toml sets and no manifest records, so it is matched
    to the directory or repository name: a pack named otherwise reads as
    unresolved, never as provided."""
    parsed = urlsplit(node.repository)
    path = parsed.path.strip("/")
    if key == "aux_id":
        return parsed.hostname == "github.com" and path.casefold() == pack.casefold()
    return pack.casefold() in (node.name.casefold(), path.rpartition("/")[2].casefold())


def report(
    found: Needs, providers: dict[str, list[str]], node_sets: dict[str, NodeSet], comfyui_ref: str
) -> list[tuple[str, bool]]:
    """One line per model file, node pack, and unknown node, and whether it resolved."""
    lines: list[tuple[str, bool]] = []
    for directory, name in found.models:
        if directory:
            matches = [destination for destination in providers if destination == f"{directory}/{name}"]
        else:
            matches = [destination for destination in providers if destination.partition("/")[2] == name]
        if not matches:
            lines.append((f"model {directory + '/' if directory else ''}{name}: no model set provides it", False))
        lines += [(f"model {match}: {', '.join(providers[match])}", True) for match in matches]
    lines.sort()
    for (key, pack), kinds in sorted(found.packs.items()):
        sets = [node_set.name for node_set in node_sets.values() if any(provides(n, key, pack) for n in node_set.nodes)]
        subject = f"node pack {pack} ({', '.join(sorted(kinds))})"
        lines.append((f"{subject}: {', '.join(sets) or 'no custom-node set provides it'}", bool(sets)))
    lines += [
        (f"node {kind}: {reason} not a core node at {comfyui_ref}", False)
        for kind, reason in sorted(found.unknown.items())
    ]
    return lines


def load_core_nodes(config_dir: Path) -> frozenset[str]:
    """The core node types; the file's first line names the ComfyUI commit."""
    return frozenset(line for line in read_utf8(config_dir / CORE_NODES_FILE).splitlines()[1:] if line)


def check(
    selected: list[WorkflowSet],
    model_sets: dict[str, ModelSet],
    node_sets: dict[str, NodeSet],
    core_nodes: frozenset[str],
    comfyui_ref: str,
) -> int:
    """Report each selected set and, per workflow, the model files and node
    packs it loads with the sets that provide them. Returns 1 when a model
    file or a node pack is provided by no set, or a node without a pack id
    or with comfy-core is no core node of the pinned ComfyUI (official templates leave the ids
    out on core nodes, and many shared workflows on custom ones)."""
    providers: dict[str, list[str]] = {}
    for model_set in model_sets.values():
        label = model_set.name + (" (local)" if model_set.private else "")
        for model_file in model_set.files:
            providers.setdefault(model_file.destination, []).append(label)
    result = 0
    for workflow_set in selected:
        print(f"{workflow_set.name}: {len(workflow_set.workflows)} workflow(s)")
        for workflow in workflow_set.workflows:
            where = f"{workflow_set.name}/{workflow.file}"
            found = needs(read_workflow_json(workflow_set.directory / workflow.file, where), where, core_nodes)
            for line, resolved in report(found, providers, node_sets, comfyui_ref):
                print(f"{where}: {line}")
                result |= not resolved
    return int(result)
