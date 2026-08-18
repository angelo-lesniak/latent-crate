from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "services" / "comfy" / "create-node-package-lock.py"


def write_wheel(path: Path, name: str, version: str) -> None:
    dist_info = f"{name.replace('-', '_')}-{version}.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{dist_info}/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        )


class NodePackageLockTests(unittest.TestCase):
    def run_lock(self, wheelhouse: Path, output: Path):
        base = wheelhouse / "base.txt"
        if not base.exists():
            base.write_text("", encoding="utf-8")
        satisfied = wheelhouse / "base-satisfied.txt"
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(wheelhouse),
                str(output),
                "--base-constraints",
                str(base),
                "--base-satisfied-output",
                str(satisfied),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_writes_a_deterministic_exact_package_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_wheel(root / "zeta-2-py3-none-any.whl", "Zeta", "2")
            write_wheel(root / "alpha_name-1.2-py3-none-any.whl", "alpha_name", "1.2")
            output = root / "install-requirements.txt"

            result = self.run_lock(root, output)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "alpha_name==1.2\nZeta==2\n")

    def test_omits_packages_already_satisfied_by_the_base_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_wheel(root / "numpy-2.3-py3-none-any.whl", "numpy", "2.3")
            write_wheel(root / "overlay-1-py3-none-any.whl", "overlay", "1")
            (root / "base.txt").write_text("NumPy==2.3\n", encoding="utf-8")
            output = root / "install-requirements.txt"

            result = self.run_lock(root, output)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "overlay==1\n")
            self.assertEqual(
                (root / "base-satisfied.txt").read_text(encoding="utf-8"),
                "numpy==2.3\n",
            )

    def test_rejects_a_wheel_that_conflicts_with_the_base_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_wheel(root / "numpy-3-py3-none-any.whl", "numpy", "3")
            (root / "base.txt").write_text("numpy==2.3\n", encoding="utf-8")

            result = self.run_lock(root, root / "install-requirements.txt")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("conflicts with base environment", result.stderr)

    def test_rejects_multiple_versions_of_the_same_distribution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_wheel(root / "demo-1-py3-none-any.whl", "demo", "1")
            write_wheel(root / "demo-2-py3-none-any.whl", "Demo", "2")
            output = root / "install-requirements.txt"

            result = self.run_lock(root, output)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("multiple wheels", result.stderr)

    def test_rejects_duplicate_wheels_at_the_same_name_and_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_wheel(root / "demo-1-py3-none-any.whl", "demo", "1")
            write_wheel(root / "demo-1-1-py3-none-any.whl", "Demo", "1")
            output = root / "install-requirements.txt"

            result = self.run_lock(root, output)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("multiple wheels", result.stderr)


if __name__ == "__main__":
    unittest.main()
