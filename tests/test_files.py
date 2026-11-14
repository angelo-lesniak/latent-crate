"""Atomic writes (the whole text appears under its name or nothing does) and UTF-8 reads."""

from __future__ import annotations

import errno
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from latentcrate import LatentCrateError, files


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
        full = OSError(errno.ENOSPC, "No space left on device")
        with mock.patch("latentcrate.files.os.replace", side_effect=full):
            with self.assertRaisesRegex(LatentCrateError, f"^cannot write {path}: No space left on device$"):
                files.write_atomic(path, "new\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
        self.assertEqual(os.listdir(self.tmp), ["settings.env"])

    def test_a_parent_that_cannot_be_made_is_the_same_one_sentence(self) -> None:
        path = self.tmp / "file" / "settings.env"
        path.parent.write_text("")
        with self.assertRaisesRegex(LatentCrateError, f"^cannot write {path}: File exists$"):
            files.write_atomic(path, "new\n")


class ReadUtf8Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="latentcrate-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_text_is_returned_without_a_byte_order_mark(self) -> None:
        path = self.tmp / "hosts.txt"
        path.write_bytes(b"\xef\xbb\xbfgithub.com\n")
        self.assertEqual(files.read_utf8(path), "github.com\n")

    def test_bytes_that_are_not_utf8_are_one_refusal_and_a_missing_file_stays_an_oserror(self) -> None:
        path = self.tmp / "hosts.txt"
        path.write_bytes(b"\xff")
        with self.assertRaisesRegex(LatentCrateError, f"^{path} is not UTF-8 text$"):
            files.read_utf8(path)
        with self.assertRaises(FileNotFoundError):
            files.read_utf8(self.tmp / "absent")


if __name__ == "__main__":
    unittest.main()
