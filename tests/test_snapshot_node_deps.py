from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "snapshot-node-deps.py"


class SnapshotNodeDependenciesTests(unittest.TestCase):
    def snapshot_command(
        self, source: Path | list[Path], destination: Path, include: Path
    ) -> list[str]:
        sources = [source] if isinstance(source, Path) else source
        source_arguments = [argument for path in sources for argument in ("--source", str(path))]
        return [
            sys.executable,
            str(SCRIPT),
            *source_arguments,
            "--destination",
            str(destination),
            "--include-file",
            str(include),
            "--vcs-pins",
            str(PROJECT_ROOT / "config" / "custom-nodes" / "vcs-pins.toml"),
            "--package-replacements",
            str(
                PROJECT_ROOT
                / "config"
                / "python"
                / "custom-node-package-replacements.toml"
            ),
            "--allowed-git-hosts",
            str(PROJECT_ROOT / "config" / "custom-nodes" / "allowed-git-hosts.txt"),
        ]

    def run_snapshot(
        self, source: Path | list[Path], destination: Path, include: Path
    ):
        return subprocess.run(
            self.snapshot_command(source, destination, include),
            check=False,
            capture_output=True,
            text=True,
        )

    def test_concurrent_writers_publish_complete_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            for index in range(80):
                node = source / f"Node{index:03d}"
                node.mkdir(parents=True)
                (node / "requirements.txt").write_text(
                    f"fixture-package-{index}==1\n", encoding="utf-8"
                )
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"
            command = self.snapshot_command(source, destination, include)

            first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            second = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            first_stdout, first_stderr = first.communicate(timeout=30)
            second_stdout, second_stderr = second.communicate(timeout=30)

            self.assertEqual(first.returncode, 0, first_stderr or first_stdout)
            self.assertEqual(second.returncode, 0, second_stderr or second_stdout)
            manifest = (destination / "manifest.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(manifest), 80)
            self.assertFalse(list(root.glob(".custom-node-requirements.staging-*")))

    def test_collects_runtime_requirements_and_relative_includes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            node = source / "Example"
            node.mkdir(parents=True)
            (node / "requirements.txt").write_text("-r deps/base.txt\n", encoding="utf-8")
            (node / "requirements-dev.txt").write_text("not-for-runtime\n", encoding="utf-8")
            (node / "deps").mkdir()
            (node / "deps" / "base.txt").write_text("packaging==25.0\n", encoding="utf-8")
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"

            result = self.run_snapshot(source, destination, include)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((destination / "Example" / "requirements.txt").is_file())
            self.assertTrue((destination / "Example" / "deps" / "base.txt").is_file())
            self.assertFalse((destination / "Example" / "requirements-dev.txt").exists())
            self.assertEqual(
                (destination / "manifest.txt").read_text(encoding="utf-8"),
                "Example/requirements.txt\n",
            )

    def test_replaces_all_opencv_variants_with_one_contrib_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            node = source / "Example"
            node.mkdir(parents=True)
            (node / "requirements.txt").write_text(
                "opencv-python\n"
                "opencv-python-headless[ffmpeg]>=4.10 ; python_version >= \"3.10\"  # video\n"
                "opencv-contrib-python-headless==5.0.0.93\n"
                "opencv-contrib-python\n",
                encoding="utf-8",
            )
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"

            result = self.run_snapshot(source, destination, include)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (destination / "Example" / "requirements.txt").read_text(
                    encoding="utf-8"
                ),
                "opencv-contrib-python\n"
                "opencv-contrib-python>=4.10 ; python_version >= \"3.10\"  # video\n"
                "opencv-contrib-python==5.0.0.93\n"
                "opencv-contrib-python\n",
            )
            rewrite_records = (
                destination / "package-rewrites.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rewrite_records), 3)
            self.assertTrue(all("reason" in record for record in rewrite_records))
            self.assertIn("Applied 3 reviewed package replacement(s)", result.stdout)

    def test_rejects_hidden_custom_node_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node = root / "custom_nodes" / ".Hidden"
            node.mkdir(parents=True)
            (node / "requirements.txt").write_text("packaging==25.0\n", encoding="utf-8")
            include = root / "include"
            include.write_text("", encoding="utf-8")

            result = self.run_snapshot(root / "custom_nodes", root / "custom-node-requirements", include)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("hidden custom-node directories", result.stderr)

    @unittest.skipIf(not hasattr(Path, "symlink_to"), "symlinks are unavailable")
    def test_rejects_a_top_level_development_node_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            source.mkdir()
            checkout = root / "checkout"
            checkout.mkdir()
            (checkout / "requirements.txt").write_text("packaging==25.0\n", encoding="utf-8")
            try:
                (source / "DevNode").symlink_to(checkout, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink creation is unavailable: {error}")
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"

            result = self.run_snapshot(source, destination, include)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("top-level custom-node symlinks are not allowed", result.stderr)

    def test_rejects_embedded_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            node = source / "Example"
            node.mkdir(parents=True)
            (node / "requirements.txt").write_text(
                "package @ git+https://user:secret@example.invalid/repo.git\n",
                encoding="utf-8",
            )
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"

            result = self.run_snapshot(source, destination, include)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("possible credential", result.stderr)

    def test_configured_runtime_alternative_is_an_entrypoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            node = source / "Example"
            node.mkdir(parents=True)
            (node / "requirements-cuda.txt").write_text("packaging==25.0\n", encoding="utf-8")
            include = root / "include"
            include.write_text("Example/requirements-cuda.txt\n", encoding="utf-8")
            destination = root / "custom-node-requirements"

            result = self.run_snapshot(source, destination, include)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (destination / "manifest.txt").read_text(encoding="utf-8"),
                "Example/requirements-cuda.txt\n",
            )

    def test_escape_failure_preserves_the_last_valid_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            node = source / "Example"
            node.mkdir(parents=True)
            (root / "outside.txt").write_text("packaging==25.0\n", encoding="utf-8")
            (node / "requirements.txt").write_text("-r ../../outside.txt\n", encoding="utf-8")
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"
            destination.mkdir()
            (destination / "last-valid.txt").write_text("preserve me\n", encoding="utf-8")

            result = self.run_snapshot(source, destination, include)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("escapes its custom-node root", result.stderr)
            self.assertTrue((destination / "last-valid.txt").is_file())
            self.assertFalse(list(root.glob(".custom-node-requirements.staging-*")))

    def test_missing_source_preserves_the_last_valid_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "missing-custom_nodes"
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"
            destination.mkdir()
            (destination / "last-valid.txt").write_text("preserve me\n", encoding="utf-8")

            result = self.run_snapshot(source, destination, include)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source directory does not exist", result.stderr)
            self.assertTrue((destination / "last-valid.txt").is_file())

    def test_configured_pattern_must_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            source.mkdir()
            include = root / "include"
            include.write_text("Missing/requirements-cuda.txt\n", encoding="utf-8")
            destination = root / "custom-node-requirements"

            result = self.run_snapshot(source, destination, include)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("matched nothing", result.stderr)

    def test_successful_promotion_replaces_the_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            node = source / "Example"
            node.mkdir(parents=True)
            (node / "requirements.txt").write_text("packaging==25.0\n", encoding="utf-8")
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"
            destination.mkdir()
            (destination / "obsolete.txt").write_text("remove me\n", encoding="utf-8")

            result = self.run_snapshot(source, destination, include)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((destination / "obsolete.txt").exists())
            self.assertTrue((destination / "Example" / "requirements.txt").is_file())
            self.assertFalse((root / ".custom-node-requirements.backup").exists())

    def test_rewrites_sam2_with_its_upstream_distribution_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            node = source / "Example"
            node.mkdir(parents=True)
            original = "git+https://github.com/facebookresearch/sam2\n"
            requirement = node / "requirements.txt"
            requirement.write_text(original, encoding="utf-8")
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"

            result = self.run_snapshot(source, destination, include)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(requirement.read_text(encoding="utf-8"), original)
            effective = (destination / "Example" / "requirements.txt").read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                effective,
                "sam-2 @ git+https://github.com/facebookresearch/sam2.git@"
                "2b90b9f5ceec907a1c18123530e92e794ad901a4\n",
            )
            self.assertTrue((destination / "vcs-rewrites.jsonl").read_text(encoding="utf-8"))

    def test_rewrites_was_git_requirements_to_reviewed_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            node = source / "was-ns"
            node.mkdir(parents=True)
            original = (
                "git+https://github.com/ltdrdata/img2texture.git\n"
                "git+https://github.com/ltdrdata/cstr\n"
                "git+https://github.com/ltdrdata/ffmpy.git\n"
            )
            requirement = node / "requirements.txt"
            requirement.write_text(original, encoding="utf-8")
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"

            result = self.run_snapshot(source, destination, include)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(requirement.read_text(encoding="utf-8"), original)
            effective = (destination / "was-ns" / "requirements.txt").read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                effective,
                "img2texture @ git+https://github.com/ltdrdata/img2texture.git@"
                "d6159abea44a0b2cf77454d3d46962c8b21eb9d3\n"
                "cstr @ git+https://github.com/ltdrdata/cstr.git@"
                "0520c29a18a7a869a6e5983861d6f7a4c86f8e9b\n"
                "ffmpy @ git+https://github.com/ltdrdata/ffmpy.git@"
                "f000737698b387ffaeab7cd871b0e9185811230d\n",
            )
            rewrite_log = (destination / "vcs-rewrites.jsonl").read_text(encoding="utf-8")
            self.assertIn("d6159abea44a0b2cf77454d3d46962c8b21eb9d3", rewrite_log)
            self.assertIn("0520c29a18a7a869a6e5983861d6f7a4c86f8e9b", rewrite_log)
            self.assertIn("f000737698b387ffaeab7cd871b0e9185811230d", rewrite_log)

    def test_vcs_rewrite_preserves_an_environment_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            node = source / "Example"
            node.mkdir(parents=True)
            requirement = node / "requirements.txt"
            requirement.write_text(
                'sam-2 @ git+https://github.com/facebookresearch/sam2 ; python_version < "3.14"\n',
                encoding="utf-8",
            )
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"

            result = self.run_snapshot(source, destination, include)

            self.assertEqual(result.returncode, 0, result.stderr)
            effective = (destination / "Example" / "requirements.txt").read_text(
                encoding="utf-8"
            )
            self.assertIn('; python_version < "3.14"', effective)

    def test_does_not_parse_git_text_in_a_comment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            node = source / "Example"
            node.mkdir(parents=True)
            original = "packaging==25.0  # see git+https://example.invalid/repo\n"
            (node / "requirements.txt").write_text(original, encoding="utf-8")
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"

            result = self.run_snapshot(source, destination, include)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (destination / "Example" / "requirements.txt").read_text(encoding="utf-8"),
                original,
            )

    def test_rejects_a_different_mutable_ref_from_a_pinned_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            node = source / "Example"
            node.mkdir(parents=True)
            (node / "requirements.txt").write_text(
                "sam-2 @ git+https://github.com/facebookresearch/sam2@feature\n",
                encoding="utf-8",
            )
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"

            result = self.run_snapshot(source, destination, include)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("permits only <no revision>", result.stderr)

    def test_rejects_unreviewed_requirement_sources_and_pip_options(self):
        bad_lines = (
            "--index-url http://example.invalid/simple\n",
            "--trusted-host example.invalid\n",
            "demo @ https://example.invalid/demo.whl\n",
            "demo @ file:///tmp/demo.whl\n",
            "demo@file:/tmp/demo.whl\n",
            "demo@ftp://alice:secret@example.invalid/demo.whl\n",
            "demo@ssh://example.invalid/demo.whl\n",
            "./local-package\n",
            f"demo @ git+https://github.com/example/demo.git@{'a' * 40}?access_token=secret\n",
        )
        for bad_line in bad_lines:
            with self.subTest(requirement=bad_line.strip()), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "custom_nodes"
                node = source / "Example"
                node.mkdir(parents=True)
                (node / "requirements.txt").write_text(bad_line, encoding="utf-8")
                include = root / "include"
                include.write_text("", encoding="utf-8")
                destination = root / "custom-node-requirements"

                result = self.run_snapshot(source, destination, include)

                self.assertNotEqual(result.returncode, 0)

    def test_rejects_unreviewed_mutable_git_requirement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            node = source / "Example"
            node.mkdir(parents=True)
            (node / "requirements.txt").write_text(
                "git+https://github.com/example/unpinned.git\n", encoding="utf-8"
            )
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"
            destination.mkdir()
            (destination / "last-valid.txt").write_text("preserve me\n", encoding="utf-8")

            result = self.run_snapshot(source, destination, include)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unpinned Git requirement", result.stderr)
            self.assertTrue((destination / "last-valid.txt").is_file())

    def test_accepts_allowed_git_requirement_at_a_full_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            node = source / "Example"
            node.mkdir(parents=True)
            commit = "a" * 40
            original = f"demo @ git+https://github.com/example/demo.git@{commit}\n"
            (node / "requirements.txt").write_text(original, encoding="utf-8")
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"

            result = self.run_snapshot(source, destination, include)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (destination / "Example" / "requirements.txt").read_text(encoding="utf-8"),
                original,
            )

    def test_collects_managed_and_local_nodes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            managed = root / "managed"
            local = root / "local"
            (managed / "ManagedNode").mkdir(parents=True)
            (local / "LocalNode").mkdir(parents=True)
            (managed / "ManagedNode" / "requirements.txt").write_text(
                "packaging==25.0\n", encoding="utf-8"
            )
            (local / "LocalNode" / "requirements.txt").write_text(
                "requests==2.32.4\n", encoding="utf-8"
            )
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"

            result = self.run_snapshot([managed, local], destination, include)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (destination / "manifest.txt").read_text(encoding="utf-8"),
                "LocalNode/requirements.txt\nManagedNode/requirements.txt\n",
            )

    def test_rejects_duplicate_names_across_managed_and_local_nodes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            managed = root / "managed"
            local = root / "local"
            (managed / "SameNode").mkdir(parents=True)
            (local / "samenode").mkdir(parents=True)
            include = root / "include"
            include.write_text("", encoding="utf-8")
            destination = root / "custom-node-requirements"

            result = self.run_snapshot([managed, local], destination, include)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate custom-node directory name", result.stderr)

    def test_recovers_an_interrupted_promotion_before_scanning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            source.mkdir()
            include = root / "include"
            include.write_text("Missing/requirements.txt\n", encoding="utf-8")
            destination = root / "custom-node-requirements"
            backup = root / ".custom-node-requirements.backup"
            backup.mkdir()
            (backup / "last-valid.txt").write_text("restore me\n", encoding="utf-8")

            result = self.run_snapshot(source, destination, include)

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((destination / "last-valid.txt").is_file())
            self.assertFalse(backup.exists())

    @unittest.skipIf(not hasattr(Path, "symlink_to"), "symlinks are unavailable")
    def test_rejects_a_symlink_snapshot_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "custom_nodes"
            source.mkdir()
            include = root / "include"
            include.write_text("", encoding="utf-8")
            external = root / "external" / "custom-node-requirements"
            external.mkdir(parents=True)
            (external / "sentinel.txt").write_text("preserve me\n", encoding="utf-8")
            destination = root / "custom-node-requirements"
            try:
                destination.symlink_to(external, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink creation is unavailable: {error}")

            result = self.run_snapshot(source, destination, include)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not be a symlink", result.stderr)
            self.assertTrue((external / "sentinel.txt").is_file())


if __name__ == "__main__":
    unittest.main()
