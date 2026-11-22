"""Workflow sets: the manifest, the directory it must match, the catalog, and the offline check."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from latentcrate import PROJECT_ROOT, LatentCrateError, env, models, nodes, workflows
from latentcrate.models import ModelFile, ModelSet
from latentcrate.nodes import Node, NodeSet

CONFIG = PROJECT_ROOT / "config"
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "workflow-sets"
COMMIT = "a" * 40
SOURCE = f"https://github.com/Comfy-Org/workflow_templates/blob/{COMMIT}/templates/fixture.json"
LICENSE = f'license = {{ name = "MIT", url = "https://github.com/example/repo/blob/{COMMIT}/LICENSE" }}\n'
HEAD = 'schema = 1\ndescription = "Fixture"\n'
CUSTOM = '[[workflow]]\nfile = "a.json"\nprovenance = "custom"\n'
CLONE = f'[[workflow]]\nfile = "a.json"\nprovenance = "clone"\nsource = "{SOURCE}"\n{LICENSE}'
WORKFLOW = {"nodes": [], "links": [], "version": 0.4}
CORE_NODES = workflows.load_core_nodes(CONFIG)
COMFYUI_REF = env.load_pins(PROJECT_ROOT / "pins.env")["COMFYUI_REF"]


def model_set(name: str, *destinations: str, private: bool = False) -> ModelSet:
    files = tuple(ModelFile("example/fixture", COMMIT, item, item, 1, "a" * 64) for item in destinations)
    return ModelSet(name, "Fixture", (), (), files, private)


MODEL_SETS = {
    "fixture-models": model_set(
        "fixture-models",
        "diffusion_models/fixture-dit.safetensors",
        "loras/style/fixture-style.safetensors",
        "upscale_models/fixture-4x.pth",
    ),
    "mine": model_set("mine", "vae/fixture-vae.safetensors", private=True),
}
NODE_SETS = {
    "fixture-nodes": NodeSet(
        "fixture-nodes",
        "Fixture",
        (
            Node("comfyui-kjnodes", "https://github.com/kijai/ComfyUI-KJNodes", COMMIT),
            Node("ComfyUI-WanVideoWrapper", "https://github.com/kijai/ComfyUI-WanVideoWrapper", COMMIT),
            Node("rgthree-comfy", "https://github.com/rgthree/rgthree-comfy", COMMIT),
            Node("comfyui-videohelpersuite", "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite", COMMIT),
        ),
    )
}


def node(kind: str, *widgets: object, mode: int = 0, **properties: object) -> dict:
    """A node as the frontend saves it; a core node unless the properties name a pack."""
    return {
        "id": 1,
        "type": kind,
        "mode": mode,
        "properties": {"cnr_id": "comfy-core", **properties},
        "widgets_values": list(widgets),
    }


EQUIVALENTS = {
    "fixture-style-v1.safetensors": workflows.Equivalent("loras/style/fixture-style.safetensors", "later version"),
    "fixture-dit-fp8.safetensors": workflows.Equivalent("diffusion_models/fixture-dit.safetensors", "other precision"),
}
DIT = "fixture-dit.safetensors"
SUMMARY = "{} workflow(s) adapted, {} model(s) not applied, {} workflow(s) refused"


def checked(
    selected: list[workflows.WorkflowSet], model_sets=MODEL_SETS, node_sets=NODE_SETS, equivalents=EQUIVALENTS
) -> tuple[int, str]:
    with contextlib.redirect_stdout(io.StringIO()) as printed:
        status = workflows.check(selected, model_sets, equivalents, node_sets, CORE_NODES, COMFYUI_REF)
    return status, printed.getvalue()


def adapted(selected: list[workflows.WorkflowSet], model_sets=MODEL_SETS) -> tuple[int, list[str]]:
    with contextlib.redirect_stdout(io.StringIO()) as printed:
        status = workflows.adapt(selected, model_sets, EQUIVALENTS, CORE_NODES)
    return status, printed.getvalue().splitlines()


class WorkflowSetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def write_set(self, body: str, files: dict[str, object] | None = None, *, root: Path | None = None) -> Path:
        """`fixture.toml` with the body and its directory holding the files (default: a.json)."""
        root = root or self.tmp
        manifest = root / "fixture.toml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(body, encoding="utf-8")
        directory = root / workflows.WORKFLOWS_DIR / "fixture"
        if directory.exists():
            shutil.rmtree(directory)
        for relative, content in (files if files is not None else {"a.json": WORKFLOW}).items():
            (directory / relative).parent.mkdir(parents=True, exist_ok=True)
            (directory / relative).write_text(json.dumps(content), encoding="utf-8")
        directory.mkdir(parents=True, exist_ok=True)
        return manifest

    def read(self, body: str, files: dict[str, object] | None = None, *, private: bool = False):
        return workflows.read_manifest(self.write_set(body, files), private=private)

    def lines(self, nodes: list[dict], *, model_sets=MODEL_SETS, node_sets=NODE_SETS, **graph) -> tuple[int, list[str]]:
        """The check of one workflow holding the nodes: its status and its
        report lines without the set's line and the file's prefix."""
        workflow_set = self.read(HEAD + CUSTOM, {"a.json": {"nodes": nodes, **graph}})
        status, output = checked([workflow_set], model_sets, node_sets)
        return status, [line.removeprefix("fixture/a.json: ") for line in output.splitlines()[1:]]

    # The manifest

    def test_each_provenance_takes_exactly_its_keys(self) -> None:
        self.assertEqual(self.read(HEAD + CUSTOM).workflows, (workflows.Workflow("a.json", "custom"),))
        clone = self.read(HEAD + CLONE).workflows[0]
        self.assertEqual((clone.provenance, clone.source, clone.license.name), ("clone", SOURCE, "MIT"))
        modified = self.read(HEAD + CLONE.replace('"clone"', '"modified-clone"') + 'note = " Fewer steps "\n')
        self.assertEqual(modified.workflows[0].note, "Fewer steps")
        refused = {
            "unknown key.*: workflows": HEAD + "workflows = []\n" + CUSTOM,
            r"unknown field\(s\) in fixture: workflow 1: url": HEAD + CUSTOM + 'url = "x"\n',
            "at least one \\[\\[workflow\\]\\] entry": HEAD,
            "provenance must be custom, clone, or modified-clone": HEAD + CUSTOM.replace('"custom"', '"forked"'),
            "a custom workflow takes exactly file, provenance$": HEAD + CUSTOM + f'source = "{SOURCE}"\n',
            "a clone workflow takes exactly file, license, provenance, source$": HEAD + CLONE.replace(LICENSE, ""),
            "a modified-clone workflow takes exactly file, license, note, provenance, source$": HEAD
            + CLONE.replace('"clone"', '"modified-clone"'),
            "note must be one non-empty line": HEAD
            + CLONE.replace('"clone"', '"modified-clone"')
            + 'note = "one\\ntwo"\n',
            "source must be an immutable approved HTTPS URL": HEAD
            + CLONE.replace(SOURCE, "https://github.com/Comfy-Org/workflow_templates/blob/main/templates/x.json"),
            "workflow 1 license needs an immutable approved HTTPS URL": HEAD
            + CLONE.replace(f"blob/{COMMIT}/LICENSE", "blob/main/LICENSE"),
            "file must name a .json workflow": HEAD + CUSTOM.replace("a.json", "a.toml"),
            "file must be a relative path": HEAD + CUSTOM.replace("a.json", "../a.json"),
            "duplicate workflow: a.json": HEAD + CUSTOM + CUSTOM,
        }
        for message, body in refused.items():
            with self.subTest(message=message), self.assertRaisesRegex(LatentCrateError, message):
                self.read(body)

    def test_only_a_private_set_may_cite_a_civitai_page_as_source_or_license(self) -> None:
        for url in ("https://civitai.com/models/123", "https://civitai.com/models/123?modelVersionId=456"):
            body = HEAD + CLONE.replace(SOURCE, url)
            self.assertEqual(self.read(body, private=True).workflows[0].source, url)
            with self.subTest(url=url), self.assertRaisesRegex(LatentCrateError, "source must be an immutable"):
                self.read(body)
        page = HEAD + CLONE.replace(LICENSE, 'license = { name = "Page", url = "https://civitai.com/models/9" }\n')
        self.assertEqual(self.read(page, private=True).workflows[0].license.url, "https://civitai.com/models/9")
        with self.assertRaisesRegex(LatentCrateError, "license needs an immutable approved HTTPS URL"):
            self.read(page)

    def test_the_directory_holds_exactly_the_listed_workflows(self) -> None:
        # The directory is mounted and ComfyUI lists every file in it.
        nested = HEAD + CUSTOM + CUSTOM.replace("a.json", "sub/b.json")
        self.assertEqual(
            [item.file for item in self.read(nested, {"a.json": WORKFLOW, "sub/b.json": WORKFLOW}).workflows],
            ["a.json", "sub/b.json"],
        )
        directory = self.tmp / "workflows" / "fixture"
        with self.assertRaisesRegex(
            LatentCrateError, f"^fixture: {directory} holds files the manifest does not list: b.json$"
        ):
            self.read(HEAD + CUSTOM, {"a.json": WORKFLOW, "b.json": WORKFLOW})
        with self.assertRaisesRegex(LatentCrateError, "does not list: notes.txt$"):
            self.read(HEAD + CUSTOM, {"a.json": WORKFLOW, "notes.txt": "x"})
        with self.assertRaisesRegex(
            LatentCrateError, f"^fixture: listed workflows are missing from {directory}: a.json$"
        ):
            self.read(HEAD + CUSTOM, {})
        shutil.rmtree(directory)
        (directory / "a.json").parent.mkdir(parents=True)
        os.symlink(FIXTURES / "workflows" / "resolved" / "t2i.json", directory / "a.json")
        with self.assertRaisesRegex(LatentCrateError, "only plain files and directories may sit in"):
            workflows.read_manifest(self.tmp / "fixture.toml")
        shutil.rmtree(directory)
        with self.assertRaisesRegex(LatentCrateError, "the workflow directory is missing or not a directory"):
            workflows.read_manifest(self.tmp / "fixture.toml")
        # The set's directory itself may not be a link either.
        os.symlink(FIXTURES / "workflows" / "resolved", directory)
        with self.assertRaisesRegex(LatentCrateError, "the workflow directory is missing or not a directory"):
            workflows.read_manifest(self.tmp / "fixture.toml")

    @unittest.skipIf(os.geteuid() == 0, "root reads a directory without read permission")
    def test_an_unreadable_directory_is_an_error_not_an_empty_listing(self) -> None:
        manifest = self.write_set(HEAD + CUSTOM, {"a.json": WORKFLOW, "sub/b.json": WORKFLOW})
        sub = self.tmp / "workflows" / "fixture" / "sub"
        sub.chmod(0)
        self.addCleanup(sub.chmod, 0o755)
        with self.assertRaises(PermissionError):
            workflows.read_manifest(manifest)

    def test_all_is_no_set_name(self) -> None:
        manifest = self.write_set(HEAD + CUSTOM)
        with self.assertRaisesRegex(LatentCrateError, "^unsafe workflow-set filename: all.toml$"):
            workflows.read_manifest(manifest.rename(manifest.with_name("all.toml")))

    def test_every_entry_of_the_mounted_directory_belongs_to_a_set(self) -> None:
        self.write_set(HEAD + CUSTOM)
        (self.tmp / "workflows" / ".gitkeep").touch()
        self.assertEqual(list(workflows.load_sets(self.tmp)), ["fixture"])
        (self.tmp / "workflows" / "stray.json").write_text("{}")
        mounted = self.tmp / "workflows"
        with self.assertRaisesRegex(
            LatentCrateError,
            f"^{mounted / 'stray.json'} belongs to no workflow set; only a set's directory, named like its "
            f"manifest, or a plain .gitkeep file may sit in {mounted}$",
        ):
            workflows.load_sets(self.tmp)
        (self.tmp / "workflows" / "stray.json").unlink()
        # Only a plain .gitkeep file is exempt: a link or a directory would be mounted.
        keep = self.tmp / "workflows" / ".gitkeep"
        for replace in (lambda: os.symlink(FIXTURES / "resolved.toml", keep), keep.mkdir):
            keep.unlink() if keep.is_symlink() or keep.is_file() else keep.rmdir()
            replace()
            with self.subTest(keep=replace), self.assertRaisesRegex(LatentCrateError, ".gitkeep belongs to no"):
                workflows.load_sets(self.tmp)

    # The catalog

    def test_the_catalog_adds_private_sets_after_the_shipped_ones_and_refuses_a_shared_name(self) -> None:
        config = self.tmp / "config"
        self.write_set(HEAD + CUSTOM, root=config / "workflow-sets")
        local = self.tmp / "local"
        self.assertEqual(list(workflows.catalog(config, local)), ["fixture"])
        self.assertEqual(workflows.catalog(self.tmp / "empty"), {})
        shutil.copytree(FIXTURES, local)
        merged = workflows.catalog(config, local)
        self.assertEqual(
            [(name, item.private) for name, item in merged.items()],
            [("fixture", False), ("broken", True), ("resolved", True)],
        )
        self.write_set(HEAD + CUSTOM, root=local)
        with self.assertRaisesRegex(
            LatentCrateError,
            f"^workflow set fixture is declared twice: {config / 'workflow-sets' / 'fixture'}.toml and "
            f"{local / 'fixture'}.toml$",
        ):
            workflows.catalog(config, local)

    # The check

    def test_a_resolved_set_names_every_model_and_node_pack_with_its_provider(self) -> None:
        # Subgraph contents count; core, frontend-only, and subgraph nodes
        # are no pack; a note's text names no model; a declared model is
        # matched at its directory, a widget value below any category.
        status, output = checked([workflows.load_sets(FIXTURES)["resolved"]])
        self.assertEqual(status, 0, output)
        self.assertEqual(
            output.splitlines(),
            [
                "resolved: 2 workflow(s)",
                "resolved/t2i.json: model diffusion_models/fixture-dit.safetensors: fixture-models",
                "resolved/t2i.json: model loras/style/fixture-style.safetensors: fixture-models",
                "resolved/t2i.json: model vae/fixture-vae.safetensors: mine (local)",
                "resolved/t2i.json: node pack comfyui-kjnodes (GetImageSizeAndCount): fixture-nodes",
                "resolved/upscale/subgraph.json: model upscale_models/fixture-4x.pth: fixture-models",
                "resolved/upscale/subgraph.json: node pack kijai/ComfyUI-WanVideoWrapper (WanVideoBlockSwap): "
                "fixture-nodes",
            ],
        )

    def test_anything_no_set_provides_is_printed_and_fails_the_check(self) -> None:
        status, output = checked([workflows.load_sets(FIXTURES)["broken"]])
        self.assertEqual(status, 1)
        self.assertEqual(
            output.splitlines(),
            [
                "broken: 1 workflow(s)",
                "broken/missing.json: model absent.safetensors: no model set provides it",
                "broken/missing.json: model loras/gone.safetensors: no model set provides it",
                "broken/missing.json: node pack comfyui-fancy-sampler (FancySampler): no custom-node set provides it",
                f"broken/missing.json: node MysteryNode: no pack id and not a core node at {COMFYUI_REF}",
            ],
        )
        # Each kind fails the check on its own.
        status, output = checked([workflows.load_sets(FIXTURES)["resolved"]], node_sets={})
        self.assertEqual(status, 1)
        self.assertIn("node pack comfyui-kjnodes (GetImageSizeAndCount): no custom-node set provides it", output)
        status, output = checked([workflows.load_sets(FIXTURES)["resolved"]], model_sets={})
        self.assertEqual(status, 1)
        self.assertIn("resolved/t2i.json: model fixture-vae.safetensors: no model set provides it", output)

    def test_a_declared_model_matches_at_its_directory_and_a_widget_value_below_one_category(self) -> None:
        dit = "fixture-dit.safetensors"
        cases = [
            # Below a category, not as any path suffix.
            ([node("UNETLoader", "dit.safetensors")], 1, ["model dit.safetensors: no model set provides it"]),
            # The directory is matched exactly, without its slashes.
            (
                [node("UNETLoader", dit, models=[{"name": dit, "directory": "loras"}])],
                1,
                [f"model loras/{dit}: re-select diffusion_models/{dit} (fixture-models)"],
            ),
            (
                [node("UNETLoader", dit, models=[{"name": dit, "directory": "/diffusion_models/"}])],
                0,
                [f"model diffusion_models/{dit}: fixture-models"],
            ),
            # A node's entry counts only once a widget selects it.
            (
                [node("UNETLoader", dit, models=[{"name": "alt.safetensors", "directory": "diffusion_models"}])],
                0,
                [f"model diffusion_models/{dit}: fixture-models"],
            ),
            # The suffix in any case; a line break means text, not a model.
            ([node("LoraLoader", "Model.SafeTensors")], 1, ["model Model.SafeTensors: no model set provides it"]),
            ([node("CLIPTextEncode", "a cat\nwearing style.safetensors")], 0, []),
            # A string holding JSON is read as what it encodes; one that only looks like JSON is text.
            (
                [node("LoraLoader", json.dumps({"loras": [{"name": "style/fixture-style.safetensors", "on": True}]}))],
                0,
                ["model loras/style/fixture-style.safetensors: fixture-models"],
            ),
            ([node("LoraLoader", '[{"on": false, "lora": "off.safetensors"}]', "[not json")], 0, []),
            # The rows of a multi-model loader, unless switched off.
            (
                [
                    node(
                        "Power Lora Loader (rgthree)",
                        {},
                        {"type": "PowerLoraLoaderHeaderWidget"},
                        {"on": True, "lora": "style\\fixture-style.safetensors", "strength": 1},
                        {"on": False, "lora": "off.safetensors", "strength": 1},
                        {},
                        "",
                        cnr_id="rgthree-comfy",
                    )
                ],
                0,
                [
                    "model loras/style/fixture-style.safetensors: fixture-models",
                    "node pack rgthree-comfy (Power Lora Loader (rgthree)): fixture-nodes",
                ],
            ),
        ]
        for nodes_, status, expected in cases:
            with self.subTest(nodes=nodes_):
                self.assertEqual(self.lines(nodes_), (status, expected))
        # A model one node declares and selects and another loads bare is one line.
        twice = [
            node("UNETLoader", dit, models=[{"name": dit, "directory": "diffusion_models"}]),
            node("UNETLoader", dit),
        ]
        self.assertEqual(self.lines(twice), (0, [f"model diffusion_models/{dit}: fixture-models"]))
        # A graph's own list counts without a widget, its backslashes read as slashes.
        graph_models = [{"name": "style\\fixture-style.safetensors", "directory": "loras"}]
        self.assertEqual(
            self.lines([], models=graph_models), (0, ["model loras/style/fixture-style.safetensors: fixture-models"])
        )

    def test_notes_muted_and_bypassed_nodes_load_nothing(self) -> None:
        notes = [
            {"id": 1, "type": "Note", "properties": {}, "widgets_values": ["Use your own style.safetensors"]},
            {"id": 2, "type": "MarkdownNote", "properties": {}, "widgets_values": ["**Model:** dit.safetensors"]},
        ]
        self.assertEqual(self.lines(notes), (0, []))
        fancy = {"cnr_id": "comfyui-fancy-sampler"}
        for mode in (2, 4):
            with self.subTest(mode=mode):
                self.assertEqual(self.lines([node("FancySampler", "gone.safetensors", mode=mode, **fancy)]), (0, []))
        # Properties written as null are none.
        self.assertEqual(self.lines([{"id": 1, "type": "KSampler", "properties": None}]), (0, []))
        # A subgraph runs through its instances only.
        definitions = {"subgraphs": [{"id": "s1", "nodes": [node("FancySampler", "gone.safetensors", **fancy)]}]}
        instance = {"id": 2, "type": "s1", "mode": 4, "properties": {}}
        self.assertEqual(self.lines([instance], definitions=definitions), (0, []))
        status, found = self.lines([{**instance, "mode": 0}], definitions=definitions)
        self.assertEqual(
            (status, found),
            (
                1,
                [
                    "model gone.safetensors: no model set provides it",
                    "node pack comfyui-fancy-sampler (FancySampler): no custom-node set provides it",
                ],
            ),
        )

    def test_a_pack_is_named_by_cnr_id_then_aux_id_and_a_node_without_either_must_be_core(self) -> None:
        kj, wan = "comfyui-kjnodes", "kijai/ComfyUI-WanVideoWrapper"
        cases = [
            (node("A", cnr_id=kj, aux_id="someone/else"), f"node pack {kj} (A): fixture-nodes"),
            (node("A", cnr_id=kj.upper()), f"node pack {kj.upper()} (A): fixture-nodes"),
            (node("A", cnr_id="", aux_id=wan), f"node pack {wan} (A): fixture-nodes"),
        ]
        for entry, expected in cases:
            with self.subTest(entry=entry):
                self.assertEqual(self.lines([entry]), (0, [expected]))
        # Official templates leave the ids out on core nodes; a custom node
        # without them (most shared workflows) fails.
        self.assertEqual(self.lines([node("KSampler", cnr_id=None)]), (0, []))
        # comfy-core on a node the pinned ComfyUI lacks: saved on a newer one.
        self.assertEqual(
            self.lines([node("NewerCoreNode")]),
            (1, [f"node NewerCoreNode: comfy-core but not a core node at {COMFYUI_REF}"]),
        )
        self.assertIn("KSampler", CORE_NODES)
        self.assertFalse([kind for kind in CORE_NODES if kind.startswith("#")], "the header line is no node type")
        self.assertEqual(
            self.lines([node("A", cnr_id=None)]), (1, [f"node A: no pack id and not a core node at {COMFYUI_REF}"])
        )
        # A registry id may match the repository name instead of the directory.
        renamed = {"kj": NodeSet("kj", "Fixture", (Node("kj", "https://github.com/kijai/ComfyUI-KJNodes", COMMIT),))}
        self.assertEqual(self.lines([node("A", cnr_id=kj)], node_sets=renamed), (0, [f"node pack {kj} (A): kj"]))
        # An aux_id names a GitHub repository.
        elsewhere = {"w": NodeSet("w", "Fixture", (Node("w", f"https://gitlab.com/{wan}", COMMIT),))}
        self.assertEqual(
            self.lines([node("A", cnr_id=None, aux_id=wan)], node_sets=elsewhere),
            (1, [f"node pack {wan} (A): no custom-node set provides it"]),
        )

    def test_subgraph_definitions_are_read_at_the_root_and_a_cycle_ends(self) -> None:
        fancy = node("FancySampler", cnr_id="comfyui-fancy-sampler")
        cycle = [
            {"id": "s1", "nodes": [fancy, {"id": 2, "type": "s2"}]},
            {"id": "s2", "nodes": [{"id": 3, "type": "s1"}, {"id": 4, "type": "s2"}]},
        ]
        self.assertEqual(
            self.lines([{"id": 1, "type": "s1"}], definitions={"subgraphs": cycle}),
            (1, ["node pack comfyui-fancy-sampler (FancySampler): no custom-node set provides it"]),
        )
        # The frontend keeps every definition at the root; one nested deeper is not read.
        nested = [
            {"id": "s1", "nodes": [{"id": 2, "type": "s3"}], "definitions": {"subgraphs": [{"id": "s3", "nodes": []}]}}
        ]
        self.assertEqual(
            self.lines([{"id": 1, "type": "s1"}], definitions={"subgraphs": nested}),
            (1, [f"node s3: no pack id and not a core node at {COMFYUI_REF}"]),
        )

    def test_a_type_named_by_its_pack_resolves_through_the_node_sets(self) -> None:
        # rgthree and VideoHelperSuite name their types; their frontend-only
        # nodes in particular are saved without ids.
        rgthree, vhs = "Fast Groups Bypasser (rgthree)", "VHS_VideoCombine"
        bare = [node(rgthree, cnr_id=None), node(vhs, cnr_id=None)]
        self.assertEqual(
            self.lines(bare),
            (
                0,
                [
                    f"node pack comfyui-videohelpersuite ({vhs}): fixture-nodes",
                    f"node pack rgthree-comfy ({rgthree}): fixture-nodes",
                ],
            ),
        )
        status, found = self.lines(bare, node_sets={})
        self.assertEqual(status, 1)
        self.assertIn(f"node pack rgthree-comfy ({rgthree}): no custom-node set provides it", found)
        for kind in ("Label (rgthree", "Fast Bypasser [rgthree]", "VHSVideoCombine", "X VHS_Combine"):
            with self.subTest(kind=kind):
                self.assertEqual(
                    self.lines([node(kind, cnr_id=None)]),
                    (1, [f"node {kind}: no pack id and not a core node at {COMFYUI_REF}"]),
                )

    def test_characters_that_do_not_print_are_escaped_in_the_report(self) -> None:
        status, found = self.lines(
            [
                node("X\x07", cnr_id=None),
                node("A", "a\tb.safetensors", cnr_id="x\x1b[31m"),
                node("B", aux_id="a/b\r", cnr_id=None),
            ],
            node_sets={},
        )
        self.assertEqual(status, 1)
        self.assertEqual(
            sorted(found),
            [
                "model a\\tb.safetensors: no model set provides it",
                "node X\\x07: no pack id and not a core node at " + COMFYUI_REF,
                "node pack a/b\\r (B): no custom-node set provides it",
                "node pack x\\x1b[31m (A): no custom-node set provides it",
            ],
        )

    def test_a_workflow_that_is_not_ui_format_json_is_refused(self) -> None:
        cases = {
            "not valid JSON": "{",
            r"not a UI-format workflow \(no nodes list\)": {"1": {"class_type": "KSampler", "inputs": {}}},
            "every node needs a type": {"nodes": [{"id": 1}]},
            "every subgraph definition needs an id": {"nodes": [], "definitions": {"subgraphs": [{"nodes": []}]}},
            "models must be a list of tables": {"nodes": [{"type": "X", "properties": {"models": "x"}}]},
            "a models entry needs a name": {"nodes": [{"type": "X", "properties": {"models": [{"directory": "vae"}]}}]},
            "definitions.subgraphs must be a list": {"nodes": [], "definitions": {"subgraphs": {}}},
            "subgraph s1 has no nodes list": {"nodes": [], "definitions": {"subgraphs": [{"id": "s1"}]}},
            "node X has properties that are not a table": {"nodes": [{"type": "X", "properties": []}]},
            "every node needs a type$": {"nodes": [{"type": ""}]},
        }
        # Too long a number and too deep a nesting: the wording is the interpreter's.
        overflowing = (
            '{"nodes": [], "n": ' + "9" * 5000 + "}",
            '{"nodes": [], "d": ' + "[" * 10**5 + "]" * 10**5 + "}",
        )
        for message, content in [*cases.items(), *((r"not valid JSON \(", text) for text in overflowing)]:
            directory = self.tmp / message[:8]
            manifest = self.write_set(HEAD + CUSTOM, {"a.json": WORKFLOW}, root=directory)
            text = content if isinstance(content, str) else json.dumps(content)
            (directory / "workflows" / "fixture" / "a.json").write_text(text)
            with self.subTest(message=message), self.assertRaisesRegex(LatentCrateError, f"^fixture/a.json: {message}"):
                checked([workflows.read_manifest(manifest)])

    def test_a_model_at_another_path_or_with_an_equivalent_names_what_to_re_select(self) -> None:
        dit = "fixture-dit.safetensors"
        other = {**MODEL_SETS, "other": model_set("other", f"checkpoints/{dit}")}
        cases = [
            # The same file name below another directory, matched case-sensitively.
            (MODEL_SETS, "Minimax\\" + dit, f"re-select diffusion_models/{dit} (fixture-models)"),
            (MODEL_SETS, "Minimax/Fixture-dit.safetensors", "no model set provides it"),
            # Several destinations: every one, none picked.
            (other, f"x/{dit}", f"re-select checkpoints/{dit} (other) or diffusion_models/{dit} (fixture-models)"),
            # A URL is no file to re-select.
            (MODEL_SETS, f"https://example.com/diffusion_models/{dit}", "no model set provides it"),
            # A reviewed equivalent, with its reason.
            (
                MODEL_SETS,
                "old/fixture-style-v1.safetensors",
                "equivalent loras/style/fixture-style.safetensors (fixture-models), later version",
            ),
        ]
        for model_sets, value, outcome in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    self.lines([node("UNETLoader", value)], model_sets=model_sets),
                    (1, [f"model {workflows.model_name(value)}: {outcome}"]),
                )

    def write_equivalents(self, body: str) -> Path:
        (self.tmp / workflows.EQUIVALENTS_FILE).write_text(HEAD.replace('description = "Fixture"\n', "") + body)
        return self.tmp

    def test_the_equivalents_table_is_validated_against_the_shipped_sets(self) -> None:
        dit = 'destination = "diffusion_models/fixture-dit.safetensors"\nreason = "same model, other precision"\n'
        entry = f'[[equivalent]]\n{dit}names = ["fixture-dit-fp8.safetensors"]\n'
        self.assertEqual(
            workflows.load_equivalents(self.write_equivalents(entry), MODEL_SETS),
            {
                "fixture-dit-fp8.safetensors": workflows.Equivalent(
                    "diffusion_models/fixture-dit.safetensors", "same model, other precision"
                )
            },
        )
        reason = 'reason = "r"\n'
        cases = {
            "at least one": "",
            "destination must be a file a shipped model set downloads: 'vae/fixture-vae.safetensors'": (
                f'[[equivalent]]\ndestination = "vae/fixture-vae.safetensors"\n{reason}names = ["a.safetensors"]\n'
            ),
            "fixture-4x.pth is the name of a file a shipped model set downloads": (
                f'[[equivalent]]\n{dit}names = ["fixture-4x.pth"]\n'
            ),
            "equivalent 2: fixture-dit-fp8.safetensors is named twice": entry + entry,
            "each name must be a model file name without a directory: 'x/a.safetensors'": (
                f'[[equivalent]]\n{dit}names = ["x/a.safetensors"]\n'
            ),
            "each name must be a model file name without a directory: 'a.txt'": (
                f'[[equivalent]]\n{dit}names = ["a.txt"]\n'
            ),
            "names must list one or more file names": f"[[equivalent]]\n{dit}names = []\n",
            "reason must be one non-empty line": (
                '[[equivalent]]\ndestination = "diffusion_models/fixture-dit.safetensors"\n'
                'reason = "a\\nb"\nnames = ["a.safetensors"]\n'
            ),
            r"unknown field\(s\) in model-equivalents.toml: equivalent 1: url": entry + 'url = "x"\n',
        }
        for message, body in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(LatentCrateError, message):
                workflows.load_equivalents(self.write_equivalents(body), MODEL_SETS)

    def test_the_shipped_equivalents_resolve_against_the_shipped_sets(self) -> None:
        model_sets = models.catalog(CONFIG)
        equivalents = workflows.load_equivalents(CONFIG, model_sets)
        self.assertTrue(equivalents)
        providers = workflows.model_providers(model_sets)
        for name, equivalent in equivalents.items():
            with self.subTest(name=name):
                self.assertEqual(
                    workflows.substitutes(f"any/{name}", providers, equivalents),
                    ([equivalent.destination], equivalent.reason),
                )

    def write_raw(self, manifest: str, text: str, *, newline: str = "\n", private: bool = True):
        """The set of fixture.toml and a.json, both written as given with the line ending."""
        (self.tmp / "fixture.toml").write_bytes(manifest.replace("\n", newline).encode())
        directory = self.tmp / "workflows" / "fixture"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "a.json").write_bytes(text.replace("\n", newline).encode())
        return workflows.read_manifest(self.tmp / "fixture.toml", private=private)

    def files(self) -> tuple[str, str]:
        return (
            (self.tmp / "fixture.toml").read_bytes().decode(),
            (self.tmp / "workflows" / "fixture" / "a.json").read_bytes().decode(),
        )

    def test_adapt_edits_only_the_references_check_reads_and_marks_a_clone_modified(self) -> None:
        twins = {**MODEL_SETS, "other": model_set("other", "loras/twin.safetensors", "vae/twin.safetensors")}
        old_entry = {"name": f"Minimax/{DIT}", "directory": "checkpoints", "url": "https://example.com/x"}
        workflow = {
            "nodes": [
                node(
                    "UNETLoader",
                    "Minimax\\" + DIT,
                    "default",
                    models=[{**old_entry, "hash": "ab", "hash_type": "SHA256"}],
                ),
                node("UNETLoader", "fp8/fixture-dit-fp8.safetensors", "fp8_e4m3fn"),
                node(
                    "Power Lora Loader (rgthree)",
                    {"on": True, "lora": "old/fixture-style-v1.safetensors"},
                    {"on": False, "lora": "old/fixture-style-v1.safetensors"},
                    cnr_id="rgthree-comfy",
                ),
                node("LoraLoader", json.dumps({"loras": [{"name": "fixture-style-v1.safetensors"}]})),
                node("UpscaleModelLoader", "up/fixture-4x.pth", "x/twin.safetensors"),
                # Nodes check does not read are neither edited nor validated.
                node("UNETLoader", "Minimax/" + DIT, mode=4, models="not a list"),
            ],
            "models": [{"name": "fixture-style-v1.safetensors", "directory": "loras"}],
            "definitions": {"subgraphs": [{"id": "unused", "nodes": [{"id": 9, "widgets_values": ["x/" + DIT]}]}]},
        }
        text = json.dumps(workflow, indent=2)
        workflow_set = self.write_raw(HEAD + CLONE, text)
        status, output = adapted([workflow_set], twins)
        self.assertEqual(status, 0)
        self.assertEqual(
            output,
            [
                "fixture/a.json: model x/twin.safetensors: loras/twin.safetensors (other) or vae/twin.safetensors "
                "(other): not applied, re-select one of them",
                f"fixture/a.json: Minimax/{DIT} -> diffusion_models/{DIT}",
                "fixture/a.json: fixture-style-v1.safetensors -> loras/style/fixture-style.safetensors "
                "(equivalent: later version)",
                f"fixture/a.json: fp8/fixture-dit-fp8.safetensors -> diffusion_models/{DIT} "
                "(equivalent: other precision)",
                "fixture/a.json: old/fixture-style-v1.safetensors -> loras/style/fixture-style.safetensors "
                "(equivalent: later version)",
                "fixture/a.json: up/fixture-4x.pth -> upscale_models/fixture-4x.pth",
                "fixture/a.json: UNETLoader weight_dtype fp8_e4m3fn -> default",
                "fixture/a.json: provenance clone -> modified-clone",
                SUMMARY.format(1, 1, 0),
            ],
        )
        manifest, result = self.files()
        # Every other line keeps its bytes; a models entry is re-encoded in its own layout, without the old hashes.
        url = f"https://huggingface.co/example/fixture/resolve/{COMMIT}/"
        self.assertEqual(
            set(result.splitlines()) - set(text.splitlines()),
            set(
                [
                    f'            "name": "{DIT}",',
                    '            "directory": "diffusion_models",',
                    f'            "url": "{url}diffusion_models/{DIT}"',
                    f'        "{DIT}",',
                    f'        "{DIT}",',
                    '          "lora": "style/fixture-style.safetensors"',
                    '        "{\\"loras\\": [{\\"name\\": \\"style/fixture-style.safetensors\\"}]}"',
                    '        "fixture-4x.pth",',
                    '      "name": "style/fixture-style.safetensors",',
                    '      "directory": "loras",',
                    f'      "url": "{url}loras/style/fixture-style.safetensors"',
                ]
            ),
        )
        # Two hash lines go, a url line comes.
        self.assertEqual(len(result.splitlines()), len(text.splitlines()) - 1)
        self.assertEqual(
            json.loads(result)["nodes"][2]["widgets_values"][1]["lora"], "old/fixture-style-v1.safetensors"
        )
        self.assertEqual(json.loads(result)["nodes"][5]["widgets_values"], [f"Minimax/{DIT}"])
        self.assertEqual(
            [(item.provenance, item.note) for item in workflows.read_manifest(self.tmp / "fixture.toml").workflows],
            [("modified-clone", workflows.ADAPTED_NOTE)],
        )
        # Only the ambiguous model stays unresolved, and a second run changes nothing.
        self.assertIn("model x/twin.safetensors: re-select", checked([workflow_set], twins)[1])
        status, output = adapted([workflows.read_manifest(self.tmp / "fixture.toml", private=True)], twins)
        self.assertEqual((status, output[-1]), (0, SUMMARY.format(0, 1, 0)))
        self.assertEqual(self.files(), (manifest, result))

    def test_adapt_keeps_line_endings_modes_comments_and_escapes(self) -> None:
        text = json.dumps({"nodes": [node("UNETLoader", f"x/{DIT}"), node("CLIPTextEncode", "cut \ud83d é")]}, indent=1)
        body = HEAD + CLONE.replace('provenance = "clone"', 'provenance = "clone"  # verbatim').replace(
            'file = "a.json"', 'file = "a.json"  # upstream name'
        )
        self.write_raw(body, text, newline="\r\n")
        for path in (self.tmp / "fixture.toml", self.tmp / "workflows" / "fixture" / "a.json"):
            path.chmod(0o600)
        status, _ = adapted([workflows.read_manifest(self.tmp / "fixture.toml", private=True)])
        self.assertEqual(status, 0)
        manifest, result = self.files()
        self.assertIn(
            'provenance = "modified-clone"  # verbatim\r\nnote = "model paths adapted to LatentCrate model sets"\r\n',
            manifest,
        )
        self.assertIn('file = "a.json"  # upstream name\r\n', manifest)
        self.assertNotIn("\n", manifest.replace("\r\n", ""))
        self.assertEqual(result, text.replace("\n", "\r\n").replace(f'"x/{DIT}"', f'"{DIT}"'))
        for path in (self.tmp / "fixture.toml", self.tmp / "workflows" / "fixture" / "a.json"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_adapt_escapes_a_lone_surrogate_it_re_encodes(self) -> None:
        # The edited JSON string holds a non-ASCII character, so it is re-encoded
        # unescaped; the lone surrogate it decodes to cannot be, and is escaped.
        self.write_raw(
            HEAD + CLONE,
            '{"nodes": [{"id": 1, "type": "LoraLoader", "mode": 0, "properties": {}, '
            '"widgets_values": ["{\\"lora\\": \\"x/' + DIT + '\\", \\"t\\": \\"é \\ud83d\\"}"]}]}',
        )
        status, output = adapted([workflows.read_manifest(self.tmp / "fixture.toml", private=True)])
        self.assertEqual((status, output[-1]), (0, SUMMARY.format(1, 0, 0)))
        widget = json.loads(self.files()[1])["nodes"][0]["widgets_values"][0]
        self.assertEqual(json.loads(widget), {"lora": DIT, "t": "é \ud83d"})

    def test_adapt_rewrites_a_modified_clone_note_once_from_its_stripped_text(self) -> None:
        modified = CLONE.replace('"clone"', '"modified-clone"') + 'note = "Trimmed\\n"  # why\n'
        workflow = json.dumps({"nodes": [node("UNETLoader", f"x/{DIT}")]})
        self.write_raw(HEAD + modified, workflow)
        self.assertEqual(adapted([workflows.read_manifest(self.tmp / "fixture.toml", private=True)])[0], 0)
        self.assertIn('note = "Trimmed; model paths adapted"  # why\n', self.files()[0])
        noted = workflows.read_manifest(self.tmp / "fixture.toml", private=True)
        self.assertEqual(noted.workflows[0].note, "Trimmed; model paths adapted")
        manifest = self.files()[0]
        self.write_raw(manifest, workflow)
        status, output = adapted([noted])
        self.assertEqual((status, output[-1]), (0, SUMMARY.format(1, 0, 0)))
        self.assertEqual(self.files()[0], manifest)

    def test_adapt_refuses_a_workflow_whose_edit_it_cannot_place_exactly(self) -> None:
        workflow = json.dumps({"nodes": [node("UNETLoader", f"x/{DIT}")]})
        modified = CLONE.replace('"clone"', '"modified-clone"')
        inline = (
            f'workflow = [{{ file = "a.json", provenance = "clone", source = "{SOURCE}", '
            f'license = {{ name = "MIT", url = "https://github.com/example/repo/blob/{COMMIT}/LICENSE" }} }}]\n'
        )
        # A JSON-string widget deeper than the editor's scanner reaches, which check still reads.
        deep = json.dumps({"nodes": [node("LoraLoader", "[" * 900 + '"x/fixture-style-v1.safetensors"' + "]" * 900)]})
        for body, text in (
            (modified + 'note = """Trimmed"""\n', workflow),
            (inline, workflow),
            (CLONE, deep),
        ):
            with self.subTest(body=body, text=text[:40]):
                workflow_set = self.write_raw(HEAD + body, text)
                status, output = adapted([workflow_set])
                self.assertEqual(status, 1)
                self.assertIn("fixture/a.json: not adapted: ", output[0])
                self.assertIn("edit the model paths by hand", output[0])
                self.assertEqual(self.files(), (HEAD + body, text))
        # The parse after the edit is what refuses a text edit placed wrong.
        with self.assertRaisesRegex(LatentCrateError, "does not parse to the intended change"):
            workflows.verified('{"a": "b"}', [(6, 9, '"c"')], {"a": "x"})
        with self.assertRaisesRegex(LatentCrateError, "two edits overlap"):
            workflows.spliced("abcdef", [(0, 3, "x"), (2, 4, "y")])

    def test_adapt_gives_a_shipped_set_only_shipped_files_and_skips_a_disputed_destination(self) -> None:
        workflow = json.dumps({"nodes": [node("VAELoader", "x/fixture-vae.safetensors")]})
        self.write_raw(HEAD + CUSTOM, workflow)
        shipped = workflows.read_manifest(self.tmp / "fixture.toml")
        self.assertEqual(adapted([shipped]), (0, [SUMMARY.format(0, 0, 0)]))
        self.assertEqual(
            adapted([workflows.read_manifest(self.tmp / "fixture.toml", private=True)])[1][-1], SUMMARY.format(1, 0, 0)
        )
        self.assertEqual(self.files()[1], workflow.replace("x/fixture-vae", "fixture-vae"))
        disputed = {
            **MODEL_SETS,
            "other": ModelSet(
                "other",
                "Fixture",
                (),
                (),
                (ModelFile("other/repo", COMMIT, DIT, f"diffusion_models/{DIT}", 2, "b" * 64),),
            ),
        }
        self.write_raw(HEAD + CUSTOM, json.dumps({"nodes": [node("UNETLoader", f"x/{DIT}")]}))
        self.assertEqual(
            adapted([shipped], disputed),
            (
                0,
                [
                    f"fixture/a.json: model x/{DIT}: diffusion_models/{DIT} (fixture-models, other): not applied, "
                    "the model sets declare it differently",
                    SUMMARY.format(0, 1, 0),
                ],
            ),
        )

    def test_a_models_entry_gets_a_civitai_url_only_for_a_pinned_file(self) -> None:
        entry = {"name": "x/c.safetensors", "directory": "loras", "url": "https://example.com/old"}
        for civitai_file, url in ((7, "https://civitai.com/api/download/models/5?fileId=7"), (None, None)):
            with self.subTest(civitai_file=civitai_file):
                civitai = ModelFile(None, None, None, "loras/c.safetensors", 1, "a" * 64, 5, civitai_file)
                sets = {"c": ModelSet("c", "Fixture", (), (), (civitai,))}
                self.write_raw(HEAD + CUSTOM, json.dumps({"nodes": [], "models": [entry]}))
                self.assertEqual(adapted([workflows.read_manifest(self.tmp / "fixture.toml")], sets)[0], 0)
                written = json.loads(self.files()[1])["models"][0]
                self.assertEqual(written.get("url"), url)
                self.assertEqual(written["name"], "c.safetensors")

    def test_every_shipped_set_resolves_against_the_shipped_model_and_node_sets(self) -> None:
        shipped = workflows.catalog(CONFIG)
        status, output = checked(
            list(shipped.values()), models.catalog(CONFIG), nodes.catalog(CONFIG, nodes.load_allowed_hosts(CONFIG))
        )
        self.assertEqual(status, 0, output)


if __name__ == "__main__":
    unittest.main()
