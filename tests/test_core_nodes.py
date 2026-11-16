from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "core-nodes.py"

NODES_PY = """
NODE_CLASS_MAPPINGS = {"CoreNode": object}

async def init_builtin_extra_nodes():
    extras_files = ["nodes_extra.py"]
"""
EXTRA = """
class Base(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="Shared")

class Child(Base):
    pass

class Listed(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="Listed")

class Hidden(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="HiddenTestNode")

class Extension(ComfyExtension):
    async def get_node_list(self):
        return [
            Listed,
            Child,
            # Hidden,
        ]
"""


class CoreNodesTests(unittest.TestCase):
    def run_script(self, extra: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "comfy_extras").mkdir()
            (root / "nodes.py").write_text(NODES_PY, encoding="utf-8")
            (root / "comfy_extras" / "nodes_extra.py").write_text(extra, encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-I", str(SCRIPT), str(root), "c0ffee"], check=False, capture_output=True, text=True
            )

    def test_an_extension_contributes_only_the_classes_its_node_list_returns(self) -> None:
        # Child inherits Base's id; Hidden is defined but left out of the list.
        result = self.run_script(EXTRA)
        self.assertEqual((result.returncode, result.stdout), (0, "# ComfyUI c0ffee\nCoreNode\nListed\nShared\n"))

    def test_a_node_list_name_without_a_class_is_refused(self) -> None:
        result = self.run_script(EXTRA.replace("            Child,", "            Child,\n            Imported,"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("get_node_list names Imported, which is no class of the module", result.stderr)

    def test_a_node_list_the_reader_cannot_resolve_is_refused(self) -> None:
        unreadable = "get_node_list is not one method returning a list of class names"
        cases = {
            "a spread": (EXTRA.replace("            Child,", "            Child,\n            *OTHERS,"), unreadable),
            "a conditional return": (
                EXTRA.replace("        return [", "        if FLAG:\n            return []\n        return ["),
                unreadable,
            ),
            "two methods": (EXTRA + "\nclass Other:\n    def get_node_list(self):\n        return []\n", unreadable),
            "a class without an id": (
                EXTRA.replace("            Child,", "            Child,\n            Plain,")
                + "\nclass Plain:\n    pass\n",
                "class Plain names 0 node ids, not one",
            ),
            "a class inheriting two ids": (
                EXTRA.replace("            Child,", "            Child,\n            Both,")
                + "\nclass Both(Listed, Base):\n    pass\n",
                "class Both names 2 node ids, not one",
            ),
        }
        for case, (extra, message) in cases.items():
            with self.subTest(case=case):
                result = self.run_script(extra)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main()
