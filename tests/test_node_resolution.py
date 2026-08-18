from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "services" / "comfy" / "create-node-resolution.py"


class NodeResolutionTests(unittest.TestCase):
    def resolve(self, report: object):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        report_path = root / "report.json"
        output = root / "requirements.txt"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(report_path), str(output)],
            check=False,
            capture_output=True,
            text=True,
        )
        return temporary, output, result

    def test_empty_install_plan_needs_no_overlay_packages(self):
        temporary, output, result = self.resolve({"install": []})
        with temporary:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "")

    def test_writes_sorted_exact_missing_packages(self):
        temporary, output, result = self.resolve(
            {
                "install": [
                    {"metadata": {"name": "Zeta", "version": "2"}},
                    {"metadata": {"name": "alpha_name", "version": "1.2"}},
                ]
            }
        )
        with temporary:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "alpha_name==1.2\nZeta==2\n")

    def test_preserves_a_resolved_full_commit_git_source(self):
        commit = "a" * 40
        temporary, output, result = self.resolve(
            {
                "install": [
                    {
                        "metadata": {"name": "demo", "version": "1"},
                        "download_info": {
                            "url": "https://github.com/example/demo.git",
                            "vcs_info": {"vcs": "git", "commit_id": commit},
                        },
                    }
                ]
            }
        )
        with temporary:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                f"demo @ git+https://github.com/example/demo.git@{commit}\n",
            )

    def test_rejects_credentials_in_a_resolved_vcs_source(self):
        temporary, _, result = self.resolve(
            {
                "install": [
                    {
                        "metadata": {"name": "demo", "version": "1"},
                        "download_info": {
                            "url": "https://user:secret@github.com/example/demo.git",
                            "vcs_info": {"vcs": "git", "commit_id": "a" * 40},
                        },
                    }
                ]
            }
        )
        with temporary:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe VCS resolution", result.stderr)


if __name__ == "__main__":
    unittest.main()
