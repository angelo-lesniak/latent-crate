from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "services" / "comfy" / "rewrite-node-requirements.py"
COMMIT = "a" * 40


class RewriteNodeRequirementsTests(unittest.TestCase):
    def run_rewrite(self, tree: Path):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(tree),
                "--vcs-pins",
                str(PROJECT_ROOT / "config" / "custom-nodes" / "vcs-pins.toml"),
                "--package-replacements",
                str(PROJECT_ROOT / "config" / "python" / "custom-node-package-replacements.toml"),
                "--allowed-git-hosts",
                str(PROJECT_ROOT / "config" / "custom-nodes" / "allowed-git-hosts.txt"),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def write_tree(self, root: Path, files: dict[str, str], manifest: str | None = None) -> Path:
        tree = root / "custom-node-requirements"
        tree.mkdir()
        for relative, text in files.items():
            path = tree / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        if manifest is None:
            manifest = "".join(f"{name}\n" for name in sorted(files))
        (tree / "manifest.txt").write_text(manifest, encoding="utf-8")
        return tree

    def test_rewrites_a_raw_tree_and_is_idempotent(self):
        raw = (
            "git+https://github.com/ltdrdata/img2texture.git\n"
            "  opencv-python-headless[ffmpeg,extra]>=4.8 ; python_version < \"3.14\"  # cv2\n"
            "sam-2 @ git+https://github.com/facebookresearch/sam2 ; python_version < \"3.14\"\n"
            f"demo @ git+https://github.com/example/demo.git@{COMMIT}\n"
            "-e git+https://github.com/ltdrdata/img2texture.git ; python_version < \"3.14\"  # dev\n"
            "-r extra/more.txt\n"
            "numpy\r\n"
        )
        expected = (
            "img2texture @ git+https://github.com/ltdrdata/img2texture.git@"
            "d6159abea44a0b2cf77454d3d46962c8b21eb9d3\n"
            "  opencv-contrib-python[extra]>=4.8 ; python_version < \"3.14\"  # cv2\n"
            "sam-2 @ git+https://github.com/facebookresearch/sam2.git@"
            "2b90b9f5ceec907a1c18123530e92e794ad901a4 ; python_version < \"3.14\"\n"
            f"demo @ git+https://github.com/example/demo.git@{COMMIT}\n"
            "-e img2texture @ git+https://github.com/ltdrdata/img2texture.git@"
            "d6159abea44a0b2cf77454d3d46962c8b21eb9d3 ; python_version < \"3.14\"  # dev\n"
            "-r extra/more.txt\n"
            "numpy\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            tree = self.write_tree(
                Path(directory),
                {
                    "Example/requirements.txt": raw,
                    "Example/extra/more.txt": "opencv-python==4.9\n",
                    "Unlisted/requirements.txt": "--index-url http://example.invalid/simple\n",
                },
                manifest="Example/requirements.txt\n",
            )

            result = self.run_rewrite(tree)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("validated 2 custom-node requirement file(s)", result.stdout)
            requirement = tree / "Example" / "requirements.txt"
            include = tree / "Example" / "extra" / "more.txt"
            self.assertEqual(requirement.read_text(encoding="utf-8"), expected)
            self.assertEqual(include.read_text(encoding="utf-8"), "opencv-contrib-python==4.9\n")

            second = self.run_rewrite(tree)

            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(requirement.read_text(encoding="utf-8"), expected)
            self.assertEqual(include.read_text(encoding="utf-8"), "opencv-contrib-python==4.9\n")

    def test_an_empty_tree_without_a_manifest_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory) / "custom-node-requirements"
            tree.mkdir()

            result = self.run_rewrite(tree)

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_refuses_unreviewed_sources_and_options(self):
        bad_lines = {
            "--index-url http://example.invalid/simple\n": "pip source and resolver options",
            "--trusted-host example.invalid\n": "pip source and resolver options",
            "-e ./local-package\n": "pip source and resolver options",
            "-e git+https://github.com/example/unpinned.git\n": "unpinned Git requirement",
            "--editable git+https://gitlab.com/ltdrdata/cstr.git\n": "allowed-git-hosts.txt",
            "demo @ https://example.invalid/demo.whl\n": "direct URL requirements",
            "demo @ file:///tmp/demo.whl\n": "direct URL requirements",
            "./local-package\n": "invalid custom-node requirement",
            "git+https://github.com/example/unpinned.git\n": "unpinned Git requirement",
            "git+https://gitlab.com/ltdrdata/cstr.git\n": "allowed-git-hosts.txt",
            "git+ssh://git@github.com/ltdrdata/cstr.git\n": "possible credential",
            "git+https://github.com/ltdrdata/cstr.git@main\n": "permits only <no revision>",
            "other @ git+https://github.com/ltdrdata/cstr.git\n": "does not match requirement package",
            f"demo @ git+https://github.com/example/demo.git@{COMMIT}?access_token=x\n": "must not contain a query",
            "demo @ git+https://github.com/example/demo.git@main \\\n": "multiline VCS requirements",
            "numpy \\\n": "invalid custom-node requirement",
            "demo @ https://ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaa@example.invalid/x\n": "possible credential",
        }
        for bad_line, message in bad_lines.items():
            with self.subTest(requirement=bad_line.strip()), tempfile.TemporaryDirectory() as directory:
                tree = self.write_tree(Path(directory), {"Example/requirements.txt": bad_line})

                result = self.run_rewrite(tree)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(result.stderr.count("LatentCrate: "), 1)
                self.assertEqual((tree / "Example" / "requirements.txt").read_text(encoding="utf-8"), bad_line)

    def test_refuses_an_include_that_escapes_or_is_missing(self):
        cases = {
            "-r ../../outside.txt\n": "escapes its custom-node directory",
            "-r /etc/passwd\n": "escapes its custom-node directory",
            "-r ../Other/requirements.txt\n": "escapes its custom-node directory",
            "-r linked/outside.txt\n": "escapes its custom-node directory",
            "-r missing.txt\n": "missing from the build input",
        }
        for include, message in cases.items():
            with self.subTest(include=include.strip()), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "outside.txt").write_text("numpy\n", encoding="utf-8")
                tree = self.write_tree(
                    root,
                    {"Example/requirements.txt": include, "Other/requirements.txt": "numpy\n"},
                    manifest="Example/requirements.txt\n",
                )
                (tree / "Example" / "linked").symlink_to(root)

                result = self.run_rewrite(tree)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_refuses_a_symlinked_requirement_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "outside.txt").write_text("numpy\n", encoding="utf-8")
            tree = self.write_tree(root, {}, manifest="Example/requirements.txt\n")
            (tree / "Example").mkdir()
            (tree / "Example" / "requirements.txt").symlink_to(root / "outside.txt")

            result = self.run_rewrite(tree)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing from the build input", result.stderr)

    def test_refuses_an_unsafe_manifest_path(self):
        for entry in ("../outside.txt", "/etc/passwd", "Example/a\0b.txt"):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as directory:
                tree = self.write_tree(Path(directory), {}, manifest=f"{entry}\n")

                result = self.run_rewrite(tree)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unsafe requirement manifest path", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_refuses_a_missing_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_rewrite(Path(directory) / "absent")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("is not a directory", result.stderr)


if __name__ == "__main__":
    unittest.main()
