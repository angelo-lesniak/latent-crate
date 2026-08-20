from __future__ import annotations

import importlib.util
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "manage-templates.py"


def load_module():
    spec = importlib.util.spec_from_file_location("latentcrate_manage_templates", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_local_catalog_filter_uses_metadata_and_bundle(self):
        entries = [
            {"name": "local", "title": "Local", "description": "Local", "openSource": True},
            {
                "name": "with_nodes",
                "title": "Nodes",
                "description": "Nodes",
                "openSource": True,
                "includeOnDistributions": ["local"],
                "requiresCustomNodes": ["example-node"],
            },
            {
                "name": "api_name_but_local_bundle",
                "title": "Prefix is not proof",
                "description": "Prefix is not proof",
                "openSource": True,
            },
            {"name": "cloud", "description": "Cloud", "openSource": True, "includeOnDistributions": ["cloud"]},
            {"name": "closed", "description": "Closed", "openSource": False},
            {"name": "old", "description": "Old", "openSource": True, "status": "archived"},
            {"name": "api_bundle", "description": "API", "openSource": True},
            {"name": "not_installed", "description": "Missing", "openSource": True},
        ]
        workflows = {
            name: ("media-api" if name == "api_bundle" else "media-image", Path(f"/{name}.json"))
            for name in (
                "local",
                "with_nodes",
                "api_name_but_local_bundle",
                "cloud",
                "closed",
                "old",
                "api_bundle",
            )
        }

        result = self.module.local_templates(
            [{"type": "image", "templates": entries}], workflows
        )

        self.assertEqual(
            [item.template_id for item in result],
            ["api_name_but_local_bundle", "local", "with_nodes"],
        )
        self.assertEqual(result[-1].custom_nodes, ("example-node",))

    def test_model_hints_extract_hugging_face_data_and_keep_unsupported_urls(self):
        digest = "a" * 64
        model = {
            "name": "model.safetensors",
            "url": "https://huggingface.co/example/models/resolve/main/files/model.safetensors?download=true",
            "directory": "diffusion_models",
            "size": 123,
            "hash": digest.upper(),
        }
        workflow = {
            "nodes": [
                {"properties": {"models": [model]}},
                {"definitions": {"nodes": [{"properties": {"models": [model]}}]}},
                {"properties": {"models": [{"url": "https://example.com/model.bin"}]}},
            ]
        }

        hints, unsupported = self.module.model_hints(workflow)

        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0].repository, "example/models")
        self.assertEqual(hints[0].source, "files/model.safetensors")
        self.assertEqual(hints[0].destination, "diffusion_models/model.safetensors")
        self.assertEqual(hints[0].sha256, digest)
        self.assertEqual(unsupported, ["https://example.com/model.bin"])

    def test_draft_exposes_missing_pins_without_weakening_manifest_rules(self):
        template = self.module.Template(
            "image_fixture",
            "Fixture",
            "Fixture description",
            "image",
            "media-image",
            (),
            Path("fixture.json"),
        )
        hint = self.module.ModelHint(
            "example/models",
            "main",
            "weights/model.safetensors",
            "checkpoints/model.safetensors",
            0,
            "",
            "https://huggingface.co/example/models/resolve/main/weights/model.safetensors",
        )

        draft = self.module.render_draft(template, "1.2.3", [hint], [])

        self.assertIn("DRAFT: models fetch will reject this file", draft)
        self.assertIn("REPLACE_WITH_40_CHARACTER_COMMIT", draft)
        self.assertIn('revision = "" # TODO:', draft)
        self.assertIn("size = 0 # TODO:", draft)
        self.assertIn('sha256 = "" # TODO:', draft)
        self.assertIn("# [[license]]", draft)
        self.assertEqual(tomllib.loads(draft)["schema"], 1)

    def test_draft_never_overwrites_an_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "draft.toml"
            self.module.write_new_file(path, "first")

            with self.assertRaisesRegex(self.module.TemplateError, "not overwritten"):
                self.module.write_new_file(path, "second")

            self.assertEqual(path.read_text(encoding="utf-8"), "first")

    def test_hugging_face_path_traversal_is_not_used_as_a_hint(self):
        raw = {
            "url": "https://huggingface.co/example/models/resolve/main/%2E%2E/secret",
            "name": "secret",
            "directory": "checkpoints",
        }

        self.assertIsNone(self.module.parse_hugging_face_hint(raw))


if __name__ == "__main__":
    unittest.main()
