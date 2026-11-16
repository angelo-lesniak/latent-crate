"""Workflow sets: the manifest, the directory it must match, the catalog, and the offline check."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
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


def checked(selected: list[workflows.WorkflowSet], model_sets=MODEL_SETS, node_sets=NODE_SETS) -> tuple[int, str]:
    with contextlib.redirect_stdout(io.StringIO()) as printed:
        status = workflows.check(selected, model_sets, node_sets, CORE_NODES, COMFYUI_REF)
    return status, printed.getvalue()


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
                [f"model loras/{dit}: no model set provides it"],
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

    def test_every_shipped_set_resolves_against_the_shipped_model_and_node_sets(self) -> None:
        shipped = workflows.catalog(CONFIG)
        status, output = checked(
            list(shipped.values()), models.catalog(CONFIG), nodes.catalog(CONFIG, nodes.load_allowed_hosts(CONFIG))
        )
        self.assertEqual(status, 0, output)


if __name__ == "__main__":
    unittest.main()
