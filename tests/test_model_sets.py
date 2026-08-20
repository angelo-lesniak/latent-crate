from __future__ import annotations

import hashlib
import importlib.util
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "manage-model-sets.py"
MANIFESTS = PROJECT_ROOT / "config" / "model-sets"


def load_module():
    spec = importlib.util.spec_from_file_location("latentcrate_manage_model_sets", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ModelSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def fixture_set(self, content: bytes = b"small pinned model"):
        model_file = self.module.ModelFile(
            repository="example/models",
            revision="a" * 40,
            source="weights/model.bin",
            destination="diffusion_models/model.bin",
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        model_set = self.module.ModelSet(
            name="fixture",
            description="Fixture",
            workflow_urls=(
                "https://github.com/Comfy-Org/workflow_templates/blob/"
                + "b" * 40
                + "/templates/fixture.json",
            ),
            licenses=(
                self.module.License(
                    "Fixture",
                    "https://huggingface.co/example/models/blob/" + "c" * 40 + "/LICENSE",
                ),
            ),
            files=(model_file,),
        )
        return model_set, model_file

    def test_shipped_catalog_is_strict_and_complete(self):
        catalog = self.module.available_sets(MANIFESTS)

        self.assertTrue(catalog)
        self.assertEqual(set(catalog), {path.stem for path in MANIFESTS.glob("*.toml")})
        self.assertTrue(all(model_set.files for model_set in catalog.values()))

    def test_all_expands_and_shared_files_are_deduplicated(self):
        catalog = self.module.available_sets(MANIFESTS)
        selected = self.module.select_sets(catalog, ["all"])
        merged = self.module.merged_files(selected)

        self.assertEqual(len(selected), len(catalog))
        self.assertLess(len(merged), sum(len(item.files) for item in selected))
        self.assertEqual(len({item.destination for item in merged}), len(merged))

    def test_all_is_reserved_as_a_manifest_name(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "all.toml"
            manifest.write_text("schema = 1\n", encoding="utf-8")

            with self.assertRaisesRegex(self.module.ModelSetError, "reserved"):
                self.module.read_manifest(manifest)

    def test_conflicting_destinations_are_rejected(self):
        model_set, model_file = self.fixture_set()
        conflicting = self.module.ModelFile(
            **{
                **model_file.__dict__,
                "repository": "other/models",
                "sha256": "f" * 64,
            }
        )
        other = self.module.ModelSet(
            "other",
            "Other",
            model_set.workflow_urls,
            model_set.licenses,
            (conflicting,),
        )

        with self.assertRaisesRegex(self.module.ModelSetError, "disagree"):
            self.module.merged_files([model_set, other])

    def test_fetch_verifies_then_atomically_promotes_a_small_fixture(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content)
        calls: list[bool] = []

        def fake_download(requested, staging, token, dry_run=False):
            self.assertEqual(requested, model_file)
            self.assertEqual(token, "fixture-token")
            calls.append(dry_run)
            if dry_run:
                self.assertIsNone(staging)
                return object()
            downloaded = staging / requested.source
            downloaded.parent.mkdir(parents=True, exist_ok=True)
            downloaded.write_bytes(content)
            return str(downloaded)

        original = self.module.hf_download
        self.module.hf_download = fake_download
        try:
            with tempfile.TemporaryDirectory() as directory:
                models_root = Path(directory)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.module.fetch([model_set], models_root, "fixture-token")
                installed = models_root / model_file.destination
                self.assertEqual(installed.read_bytes(), content)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(self.module.status([model_set], models_root), 0)
                self.assertFalse(list(models_root.rglob("*.safetensors")))
        finally:
            self.module.hf_download = original

        self.assertEqual(calls, [True, False])

    def test_wrong_existing_file_is_never_overwritten(self):
        model_set, model_file = self.fixture_set()
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            destination = models_root / model_file.destination
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"user file")

            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(self.module.ModelSetError, "move it aside"):
                    self.module.fetch([model_set], models_root, None)

            self.assertEqual(destination.read_bytes(), b"user file")

    def test_file_created_during_download_is_not_overwritten(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content)

        def fake_download(requested, staging, token, dry_run=False):
            if dry_run:
                return object()
            destination = models_root / requested.destination
            destination.write_bytes(b"user file created while downloading")
            downloaded = staging / requested.source
            downloaded.parent.mkdir(parents=True, exist_ok=True)
            downloaded.write_bytes(content)
            return str(downloaded)

        original = self.module.hf_download
        self.module.hf_download = fake_download
        try:
            with tempfile.TemporaryDirectory() as directory:
                models_root = Path(directory)
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(self.module.ModelSetError, "appeared while downloading"):
                        self.module.fetch([model_set], models_root, None)
                destination = models_root / model_file.destination
                self.assertEqual(destination.read_bytes(), b"user file created while downloading")
        finally:
            self.module.hf_download = original

    def test_unsupported_hard_links_fail_before_network_access(self):
        model_set, _ = self.fixture_set()

        def unexpected_download(*args, **kwargs):
            self.fail("network access started before the storage capability check")

        def reject_link(*args, **kwargs):
            raise OSError("hard links disabled")

        original_download = self.module.hf_download
        original_link = self.module.os.link
        self.module.hf_download = unexpected_download
        self.module.os.link = reject_link
        try:
            with tempfile.TemporaryDirectory() as directory:
                models_root = Path(directory)
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(self.module.ModelSetError, "must allow.*hard links"):
                        self.module.fetch([model_set], models_root, None)
                self.assertFalse(list(models_root.rglob(".latentcrate-link-test-*")))
        finally:
            self.module.os.link = original_link
            self.module.hf_download = original_download

    def test_integrity_failure_discards_completed_staging(self):
        model_set, model_file = self.fixture_set()

        def fake_download(requested, staging, token, dry_run=False):
            if dry_run:
                return object()
            downloaded = staging / requested.source
            downloaded.parent.mkdir(parents=True, exist_ok=True)
            downloaded.write_bytes(b"wrong pinned model")
            return str(downloaded)

        original = self.module.hf_download
        self.module.hf_download = fake_download
        try:
            with tempfile.TemporaryDirectory() as directory:
                models_root = Path(directory)
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(self.module.ModelSetError, "checksum"):
                        self.module.fetch([model_set], models_root, None)
                self.assertFalse(list(models_root.rglob(".latentcrate-downloads")))
        finally:
            self.module.hf_download = original

    def test_network_failure_preserves_partial_staging(self):
        model_set, _ = self.fixture_set()

        def fake_download(requested, staging, token, dry_run=False):
            if dry_run:
                return object()
            partial = staging / requested.source
            partial.parent.mkdir(parents=True, exist_ok=True)
            partial.write_bytes(b"partial")
            raise RuntimeError("connection interrupted")

        original = self.module.hf_download
        self.module.hf_download = fake_download
        try:
            with tempfile.TemporaryDirectory() as directory:
                models_root = Path(directory)
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(self.module.ModelSetError, "download failed"):
                        self.module.fetch([model_set], models_root, None)
                self.assertEqual([path.read_bytes() for path in models_root.rglob("model.bin")], [b"partial"])
        finally:
            self.module.hf_download = original

    def test_manifest_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "unsafe.toml"
            manifest.write_text(
                """schema = 1
description = "Unsafe"
workflow_urls = ["https://github.com/Comfy-Org/workflow_templates/blob/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/templates/test.json"]
[[license]]
name = "Test"
url = "https://huggingface.co/example/model/blob/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/LICENSE"
[[file]]
repository = "example/model"
revision = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
source = "../secret"
destination = "diffusion_models/model.bin"
size = 1
sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
""",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(self.module.ModelSetError, "safe relative path"):
                self.module.read_manifest(manifest)

    def test_status_accepts_a_verified_symlink_inside_model_storage(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content)
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            target = models_root / "shared" / "model.bin"
            target.parent.mkdir()
            target.write_bytes(content)
            destination = models_root / model_file.destination
            destination.parent.mkdir(parents=True)
            try:
                os.symlink(target, destination)
            except OSError as error:
                self.skipTest(f"symbolic links are unavailable: {error}")

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.module.status([model_set], models_root), 0)

    def test_status_reports_missing_without_creating_category_directories(self):
        model_set, _ = self.fixture_set()
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(self.module.status([model_set], models_root), 1)

            self.assertIn("diffusion_models/model.bin: missing", output.getvalue())
            self.assertFalse((models_root / "diffusion_models").exists())

    def test_status_rejects_a_symlink_outside_allowed_storage(self):
        model_set, model_file = self.fixture_set()
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            models_root = Path(directory)
            target = Path(outside) / "model.bin"
            target.write_bytes(b"small pinned model")
            destination = models_root / model_file.destination
            destination.parent.mkdir(parents=True)
            try:
                os.symlink(target, destination)
            except OSError as error:
                self.skipTest(f"symbolic links are unavailable: {error}")

            with self.assertRaisesRegex(self.module.ModelSetError, "symlink escapes"):
                self.module.status([model_set], models_root)

    def test_fetch_rejects_nested_symlinks_in_resumable_staging(self):
        model_set, model_file = self.fixture_set()
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            models_root = Path(directory)
            destination = models_root / model_file.destination
            destination.parent.mkdir(parents=True)
            key = hashlib.sha256(
                f"{model_file.repository}\0{model_file.revision}\0{model_file.source}".encode()
            ).hexdigest()[:24]
            staging = destination.parent / ".latentcrate-downloads" / key
            staging.mkdir(parents=True)
            try:
                os.symlink(Path(outside), staging / "weights", target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symbolic links are unavailable: {error}")

            def fake_download(requested, requested_staging, token, dry_run=False):
                if dry_run:
                    return object()
                self.fail("download started with an unsafe staging tree")

            original = self.module.hf_download
            self.module.hf_download = fake_download
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(self.module.ModelSetError, "symlink inside resumable staging"):
                        self.module.fetch([model_set], models_root, None)
            finally:
                self.module.hf_download = original

    def test_rejected_directory_symlink_creates_nothing_outside_storage(self):
        model_set, model_file = self.fixture_set()
        nested_file = self.module.ModelFile(
            **{**model_file.__dict__, "destination": "diffusion_models/new/model.bin"}
        )
        nested_set = self.module.ModelSet(
            model_set.name,
            model_set.description,
            model_set.workflow_urls,
            model_set.licenses,
            (nested_file,),
        )
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            models_root = Path(directory)
            try:
                os.symlink(Path(outside), models_root / "diffusion_models", target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symbolic links are unavailable: {error}")

            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(self.module.ModelSetError, "escapes"):
                    self.module.fetch([nested_set], models_root, None)
            self.assertFalse((Path(outside) / "new").exists())


if __name__ == "__main__":
    unittest.main()
