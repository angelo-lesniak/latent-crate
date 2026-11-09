"""The container entrypoint's policy, run through bash without an engine:
the frontend-mode refusals happen before the script touches a container
path, so they run the real script end to end; the custom-node and Sage
helpers run only after the fixed /data and /local roots exist, so their
function definitions are taken verbatim from the script and driven
against scratch directories."""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

from tests.support import SOURCE_ROOT, Scratch

ENTRYPOINT = SOURCE_ROOT / "services" / "comfy" / "entrypoint.sh"


def extract_function(name: str) -> str:
    """The lines of `name() {` up to its closing brace, as written."""
    lines = ENTRYPOINT.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"{name}() {{")
    end = lines.index("}", start)
    return "\n".join(lines[start : end + 1]) + "\n"


class EntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Scratch()
        self.addCleanup(self.scratch.close)
        self.scenarios = 0

    def run_bash(self, script: Path, *arguments: str, **env: str) -> subprocess.CompletedProcess:
        """Run a script under a bare environment: PATH and the variables given."""
        return subprocess.run(
            ["bash", str(script), *arguments],
            env={"PATH": os.environ["PATH"], **env},
            capture_output=True,
            text=True,
            check=False,
        )

    def assert_entrypoint_refuses(self, expected: str, **env: str) -> None:
        result = self.run_bash(ENTRYPOINT, **env)
        self.assertNotEqual(result.returncode, 0, "the entrypoint accepted the configuration")
        self.assertIn(expected, result.stderr)

    # --- Frontend-mode policy -------------------------------------------------

    def test_git_mode_is_refused_on_an_image_without_a_frontend(self) -> None:
        self.assert_entrypoint_refuses(
            "frontend mode git needs an image with a built-in frontend", COMFY_FRONTEND_MODE="git"
        )

    def test_an_unset_mode_is_git_and_refused_without_a_built_in_frontend(self) -> None:
        self.assert_entrypoint_refuses("frontend mode git needs an image with a built-in frontend")

    def test_release_is_a_host_side_name_the_container_refuses(self) -> None:
        self.assert_entrypoint_refuses("unsupported frontend mode: release", COMFY_FRONTEND_MODE="release")

    def test_the_image_frontend_must_be_served_from_its_own_root(self) -> None:
        self.assert_entrypoint_refuses(
            "image frontend mode must use /opt/latentcrate-frontend",
            COMFY_FRONTEND_MODE="git",
            LATENTCRATE_FRONTEND_IMAGE_MODE="git",
            COMFY_FRONTEND_ROOT="/somewhere/else",
        )

    def test_dist_mode_needs_the_fixed_read_only_mount(self) -> None:
        self.assert_entrypoint_refuses(
            "dist frontend mode must use the read-only mount at /opt/latentcrate-frontend-dist",
            COMFY_FRONTEND_MODE="dist",
        )

    def test_an_unknown_mode_is_refused(self) -> None:
        self.assert_entrypoint_refuses("unsupported frontend mode: cdn", COMFY_FRONTEND_MODE="cdn")

    # --- Custom-node policy -----------------------------------------------------

    def node_runner(self) -> Path:
        """The two rejection helpers, run against the roots given as arguments."""
        runner = self.scratch.tmp / "reject-runner.sh"
        runner.write_text(
            "set -uo pipefail\nDATA_ROOT=$1\nLOCAL_NODES_ROOT=$2\n"
            + extract_function("reject_hidden_custom_nodes")
            + extract_function("reject_duplicate_custom_nodes")
            + "reject_hidden_custom_nodes\nreject_duplicate_custom_nodes\n",
            encoding="utf-8",
        )
        return runner

    def node_roots(self, *directories: str, files: tuple[str, ...] = ()) -> tuple[Path, Path]:
        """Fresh managed and local roots holding the directories (and files)
        named relative to the scenario, `data/custom_nodes/<x>` or `local/<x>`."""
        self.scenarios += 1
        scenario = self.scratch.tmp / f"scenario-{self.scenarios}"
        data, local = scenario / "data", scenario / "local"
        (data / "custom_nodes").mkdir(parents=True)
        local.mkdir()
        for relative in directories:
            (scenario / relative).mkdir(parents=True)
        for relative in files:
            (scenario / relative).touch()
        return data, local

    def run_node_checks(self, data: Path, local: Path) -> subprocess.CompletedProcess:
        return self.run_bash(self.node_runner(), str(data), str(local))

    def assert_nodes_accepted(self, data: Path, local: Path) -> None:
        result = self.run_node_checks(data, local)
        self.assertEqual(result.returncode, 0, result.stderr)

    def assert_nodes_rejected(self, expected: str, data: Path, local: Path) -> None:
        result = self.run_node_checks(data, local)
        self.assertNotEqual(result.returncode, 0, "the custom-node checks passed")
        self.assertIn(expected, result.stderr)

    def test_distinct_managed_and_local_nodes_are_accepted(self) -> None:
        self.assert_nodes_accepted(*self.node_roots("data/custom_nodes/managed-node", "local/local-node"))

    def test_a_case_insensitive_duplicate_node_is_rejected(self) -> None:
        self.assert_nodes_rejected(
            "duplicate custom-node name in managed and local sources: SharedNode / sharednode",
            *self.node_roots("data/custom_nodes/SharedNode", "local/sharednode"),
        )

    def test_a_disabled_managed_copy_is_not_a_duplicate(self) -> None:
        self.assert_nodes_accepted(*self.node_roots("data/custom_nodes/shared.disabled", "local/shared"))

    def test_a_hidden_managed_node_directory_is_rejected(self) -> None:
        self.assert_nodes_rejected(
            "hidden custom-node directories are not supported", *self.node_roots("data/custom_nodes/.sneaky-node")
        )

    def test_a_hidden_local_node_directory_is_rejected(self) -> None:
        self.assert_nodes_rejected(
            "hidden custom-node directories are not supported", *self.node_roots("local/.sneaky-node")
        )

    def test_hidden_files_are_tolerated(self) -> None:
        self.assert_nodes_accepted(*self.node_roots(files=("data/custom_nodes/.gitkeep", "local/.gitkeep")))

    # --- Sage argument policy -----------------------------------------------------

    def sage_runner(self) -> Path:
        """The Sage helper, printing the arguments it appended on one line."""
        runner = self.scratch.tmp / "sage-runner.sh"
        runner.write_text(
            "set -uo pipefail\nargs=()\n"
            + extract_function("append_sage_args")
            + 'append_sage_args\nprintf "sage-args:%s\\n" "${args[*]-}"\n',
            encoding="utf-8",
        )
        return runner

    def assert_sage_args(self, expected: str, **env: str) -> None:
        result = self.run_bash(self.sage_runner(), **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"sage-args:{expected}\n")

    def assert_sage_refuses(self, expected: str, **env: str) -> None:
        result = self.run_bash(self.sage_runner(), **env)
        self.assertNotEqual(result.returncode, 0, "the Sage helper accepted the configuration")
        self.assertIn(expected, result.stderr)

    def test_the_default_sage_mode_adds_no_argument(self) -> None:
        self.assert_sage_args("")

    def test_sage_off_adds_no_argument(self) -> None:
        self.assert_sage_args("", LATENTCRATE_SAGE_MODE="off")

    def test_sage_available_adds_no_argument(self) -> None:
        self.assert_sage_args("", LATENTCRATE_SAGE_MODE="available")

    def test_sage_global_on_a_sage_image_applies_sage_attention(self) -> None:
        self.assert_sage_args("--use-sage-attention", LATENTCRATE_SAGE_MODE="global", LATENTCRATE_SAGE_ENABLED="1")

    def test_sage_global_on_an_image_without_the_marker_is_refused(self) -> None:
        self.assert_sage_refuses("requires the runtime-sage image target", LATENTCRATE_SAGE_MODE="global")

    def test_an_unknown_sage_mode_is_refused(self) -> None:
        self.assert_sage_refuses("must be off, available, or global", LATENTCRATE_SAGE_MODE="maybe")


if __name__ == "__main__":
    unittest.main()
