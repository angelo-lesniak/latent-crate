from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import importlib.util
import json
import os
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "manage-node-set.py"
ALLOWED_HOSTS = PROJECT_ROOT / "config" / "custom-nodes" / "allowed-git-hosts.txt"


def load_node_set_module():
    spec = importlib.util.spec_from_file_location("latentcrate_manage_node_set", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NodeSetTests(unittest.TestCase):
    def run_status(self, manifest: Path, target: Path):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "status",
                "--manifest",
                str(manifest),
                "--target",
                str(target),
                "--allowed-git-hosts",
                str(ALLOWED_HOSTS),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_reports_a_valid_missing_node_without_network_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "custom_nodes"
            target.mkdir()
            manifest = root / "set.toml"
            manifest.write_text(
                """version = 1
description = "Fixture"
[[node]]
name = "Example"
repository = "https://github.com/example/example.git"
commit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
""",
                encoding="utf-8",
            )

            result = self.run_status(manifest, target)

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("Example: missing", result.stdout)

    def test_rejects_a_mutable_node_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "custom_nodes"
            target.mkdir()
            manifest = root / "set.toml"
            manifest.write_text(
                """version = 1
[[node]]
name = "Example"
repository = "https://github.com/example/example.git"
commit = "main"
""",
                encoding="utf-8",
            )

            result = self.run_status(manifest, target)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("full 40-character Git commit", result.stderr)

    def test_rejects_credentials_and_unapproved_hosts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "custom_nodes"
            target.mkdir()
            for repository in (
                "https://user:secret@github.com/example/example.git",
                "https://gitlab.com/example/example.git",
            ):
                with self.subTest(repository=repository):
                    manifest = root / "set.toml"
                    manifest.write_text(
                        f"""version = 1
[[node]]
name = "Example"
repository = "{repository}"
commit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
""",
                        encoding="utf-8",
                    )

                    result = self.run_status(manifest, target)

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("credential-free HTTPS", result.stderr)

    def test_recovers_an_interrupted_multi_node_transaction(self):
        module = load_node_set_module()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "custom_nodes"
            target.mkdir()
            staging = target / ".latentcrate-node-set-fixture.disabled"
            staging.mkdir()
            (staging / ".backup-Updated").mkdir()
            (staging / ".backup-Updated" / "state").write_text("old", encoding="utf-8")
            entries = []
            for name, repository, status in (
                ("Updated", "https://github.com/example/updated", "different-commit"),
                ("Added", "https://github.com/example/added", "missing"),
            ):
                checkout = target / name
                checkout.mkdir()
                subprocess.run(["git", "init", str(checkout)], check=True, capture_output=True)
                subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin", repository], check=True)
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(checkout),
                        "-c",
                        "user.name=Test",
                        "-c",
                        "user.email=test@example.invalid",
                        "-c",
                        "commit.gpgsign=false",
                        "commit",
                        "--allow-empty",
                        "-m",
                        "fixture",
                    ],
                    check=True,
                    capture_output=True,
                    env={
                        **os.environ,
                        "GIT_CONFIG_GLOBAL": os.devnull,
                        "GIT_CONFIG_NOSYSTEM": "1",
                    },
                )
                commit = subprocess.run(
                    ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                entries.append(
                    {"name": name, "status": status, "repository": repository, "commit": commit}
                )
            (staging / "transaction.json").write_text(
                json.dumps(entries), encoding="utf-8"
            )

            module.recover_interrupted_install(target)

            self.assertEqual((target / "Updated" / "state").read_text(), "old")
            self.assertFalse((target / "Added").exists())
            self.assertFalse(staging.exists())

    def test_recovery_preserves_a_destination_changed_after_interruption(self):
        module = load_node_set_module()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "custom_nodes"
            destination = target / "Added"
            staging = target / ".latentcrate-node-set-fixture.disabled"
            destination.mkdir(parents=True)
            (destination / "user-work").write_text("preserve", encoding="utf-8")
            staging.mkdir()
            (staging / "transaction.json").write_text(
                json.dumps(
                    [
                        {
                            "name": "Added",
                            "status": "missing",
                            "repository": "https://github.com/example/added",
                            "commit": "a" * 40,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(SystemExit) as context:
                module.recover_interrupted_install(target)

            self.assertIn("recover manually", str(context.exception))
            self.assertEqual((destination / "user-work").read_text(), "preserve")
            self.assertTrue(staging.exists())

    @unittest.skipIf(not hasattr(Path, "symlink_to"), "symlinks are unavailable")
    def test_dangling_destination_symlink_is_a_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "custom_nodes"
            target.mkdir()
            try:
                (target / "Example").symlink_to(root / "missing", target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink creation is unavailable: {error}")
            manifest = root / "set.toml"
            manifest.write_text(
                """version = 1
[[node]]
name = "Example"
repository = "https://github.com/example/example.git"
commit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
""",
                encoding="utf-8",
            )

            result = self.run_status(manifest, target)

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("Example: conflict", result.stdout)

    def test_status_reports_a_pending_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "custom_nodes"
            target.mkdir()
            (target / ".latentcrate-node-set-pending.disabled").mkdir()
            manifest = root / "set.toml"
            manifest.write_text(
                """version = 1
[[node]]
name = "Example"
repository = "https://github.com/example/example.git"
commit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
""",
                encoding="utf-8",
            )

            result = self.run_status(manifest, target)

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("pending node-set transaction", result.stdout)

    def test_normalizes_equivalent_public_repository_urls(self):
        module = load_node_set_module()
        allowed = {"github.com"}

        self.assertEqual(
            module.validate_repository("https://github.com/Owner/Repo.git", allowed),
            module.validate_repository("https://github.com/owner/repo", allowed),
        )

    @unittest.skipUnless(shutil.which("git"), "git is unavailable")
    def test_rejects_execution_capable_local_git_configuration(self):
        module = load_node_set_module()
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / "Example"
            subprocess.run(["git", "init", str(checkout)], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(checkout), "config", "filter.evil.clean", "false"],
                check=True,
            )
            node = module.Node(
                "Example", "https://github.com/example/example", "a" * 40
            )

            with self.assertRaises(SystemExit) as context:
                module.existing_status(checkout, node)

            self.assertIn("execution-capable local Git configuration", str(context.exception))


if __name__ == "__main__":
    unittest.main()
