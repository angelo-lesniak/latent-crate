"""Atomic writes: the whole text appears under its name or nothing does."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from latentcrate import files


class AtomicWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="latentcrate-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_text_lands_under_its_name_with_the_mode_asked_for(self) -> None:
        path = self.tmp / "nested" / "settings.env"
        files.write_atomic(path, "A=1\n", 0o600)
        self.assertEqual(path.read_text(encoding="utf-8"), "A=1\n")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        files.write_atomic(path, "A=2\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "A=2\n")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        self.assertEqual(os.listdir(path.parent), ["settings.env"])

    def test_a_failed_write_leaves_the_previous_file_and_no_temporary(self) -> None:
        path = self.tmp / "settings.env"
        path.write_text("old\n", encoding="utf-8")
        with mock.patch("latentcrate.files.os.replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                files.write_atomic(path, "new\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
        self.assertEqual(os.listdir(self.tmp), ["settings.env"])


if __name__ == "__main__":
    unittest.main()
