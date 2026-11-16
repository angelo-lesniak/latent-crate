"""The rules every manifest kind shares."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from latentcrate import LatentCrateError, names


@dataclass
class Entry:
    name: str


class SharedRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="latentcrate-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_reader_words_the_three_manifest_failures_once(self) -> None:
        path = self.tmp / "x.toml"
        path.write_text("schema = 1\nknown = 1\n", encoding="utf-8")
        self.assertEqual(names.read_toml(path, {"schema", "known"}, "x"), {"schema": 1, "known": 1})
        with self.assertRaisesRegex(LatentCrateError, r"^x: unknown key\(s\): known$"):
            names.read_toml(path, {"schema"}, "x")
        path.write_text("schema = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(LatentCrateError, "^x: schema must be 1$"):
            names.read_toml(path, {"schema"}, "x")
        path.write_text("not toml", encoding="utf-8")
        with self.assertRaisesRegex(LatentCrateError, r"^x: cannot read the manifest \("):
            names.read_toml(path, {"schema"}, "x")
        with self.assertRaisesRegex(LatentCrateError, "cannot read the manifest"):
            names.read_toml(self.tmp / "missing.toml", {"schema"}, "x")

    def test_descriptions_and_relative_paths(self) -> None:
        self.assertEqual(names.description({"description": " one line "}, "x"), "one line")
        for value in (None, "", " ", "two\nlines", "red \x1b[31m", 3):
            with self.assertRaisesRegex(LatentCrateError, "x: description must be one non-empty line"):
                names.description({"description": value}, "x")
        self.assertEqual(
            names.relative_path("diffusion_models/demo-1.0+a@b", "path", "x"), "diffusion_models/demo-1.0+a@b"
        )
        for value in ("/abs", "../up", "a/../b", ".", "a b", "a#b", "a/", "", None):
            with self.assertRaisesRegex(LatentCrateError, "x: path must be a relative path"):
                names.relative_path(value, "path", "x")

    def test_the_catalog_is_in_set_name_order_and_selection_names_each_entry_once(self) -> None:
        for stem in ("demo-lite", "demo", "demo.extra"):
            (self.tmp / f"{stem}.toml").write_text("schema = 1\n", encoding="utf-8")
        catalog = names.load_catalog(self.tmp, lambda path: Entry(path.stem), "model set")
        self.assertEqual(list(catalog), ["demo", "demo-lite", "demo.extra"])
        self.assertEqual([entry.name for entry in names.select_sets(catalog, ["demo", "demo"], "model set")], ["demo"])
        self.assertEqual(len(names.select_sets(catalog, ["all"], "model set")), 3)
        with self.assertRaisesRegex(LatentCrateError, "^provide one or more model set names, or all$"):
            names.select_sets(catalog, [], "model set")
        with self.assertRaisesRegex(LatentCrateError, "^all cannot be combined with named model sets$"):
            names.select_sets(catalog, ["all", "demo"], "model set")
        with self.assertRaisesRegex(
            LatentCrateError, r"^unknown model set: x \(available: demo, demo-lite, demo.extra\)$"
        ):
            names.select_sets(catalog, ["x"], "model set")
        empty = self.tmp / "empty"
        empty.mkdir()
        with self.assertRaisesRegex(LatentCrateError, f"^no model sets found in {empty}$"):
            names.load_catalog(empty, lambda path: Entry(path.stem), "model set")


if __name__ == "__main__":
    unittest.main()
