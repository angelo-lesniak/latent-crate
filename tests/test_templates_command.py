"""`templates` against the fake engine: the image gate and the hardened
`engine run` of the image it issues."""

from __future__ import annotations

import os
import unittest

from latentcrate import env, templates
from tests.support import Scratch

NO_IMAGE = {"FAKE_ENGINE_IMAGE_MISSING": "true"}
HARDENING = "--read-only --cap-drop ALL --security-opt no-new-privileges --pids-limit 128 --tmpfs /tmp"


class TemplatesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Scratch()
        self.addCleanup(self.scratch.close)

    def test_a_missing_image_is_refused_before_any_run(self) -> None:
        for arguments in (("templates", "list"), ("templates", "create-model-set", "video_wan")):
            with self.subTest(arguments=arguments):
                result = self.scratch.run(*arguments, **NO_IMAGE)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("latentcrate/comfy:release-", result.stderr)
                self.assertIn(" is not built; run: bash bin/latentcrate build", result.stderr)
                self.assertNotIn("run ", result.stdout)

    def test_list_runs_the_script_in_a_networkless_release_container_without_mounts(self) -> None:
        # The fake engine exits 1 when HF_TOKEN reaches it; the token in the shell must not.
        for name, user in (("docker", f"--user {os.getuid()}:{os.getgid()} "), ("podman", "")):
            with self.subTest(engine=name):
                result = self.scratch.run(
                    "templates",
                    "list",
                    CONTAINER_ENGINE=name,
                    COMFY_FRONTEND_MODE="dist",
                    HF_TOKEN="hf_fake_token_for_tests",
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                pinned = env.load_pins(self.scratch.root / "pins.env")
                image = f"latentcrate/comfy:release-{pinned['COMFYUI_REF']}"
                self.assertIn("run stdin=devnull image=unset target=unset env_keys= args=", result.stdout)
                self.assertIn(
                    f"args=run --rm --network none {HARDENING} {user}--entrypoint python {image} "
                    "/usr/local/bin/latentcrate-manage-templates.py list",
                    result.stdout,
                )
                self.assertNotIn("compose", result.stdout)
                self.assertNotIn("--volume", result.stdout)
                self.assertNotIn("hf_fake_token_for_tests", result.stdout + result.stderr)

    def test_create_model_set_mounts_the_drafts_directory_only_and_passes_the_names(self) -> None:
        drafts = self.scratch.root / "build" / templates.DRAFTS_DIR
        result = self.scratch.run("templates", "create-model-set", "video_wan", "--name", "wan-draft")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(drafts.is_dir())
        self.assertIn(
            f"run --rm --network none {HARDENING} --user {os.getuid()}:{os.getgid()} --volume {drafts}:/output "
            "--entrypoint python latentcrate/comfy:release-",
            result.stdout,
        )
        self.assertRegex(
            result.stdout,
            r"--entrypoint python latentcrate/comfy:release-\S+ /usr/local/bin/latentcrate-manage-templates\.py "
            r"create-model-set video_wan --name wan-draft\n",
        )
        self.assertEqual(result.stdout.count("--volume"), 1)
        self.assertIn(
            "create-model-set video_wan --name video_wan",
            self.scratch.run("templates", "create-model-set", "video_wan").stdout,
        )

    def test_unsafe_names_are_refused(self) -> None:
        for arguments, expected in (
            (("templates", "create-model-set", "video_wan", "--name", "all"), "all names every model set"),
            (("templates", "create-model-set", "video_wan", "--name", "../x"), "--name requires a model set name"),
            (("templates", "create-model-set", "../x"), "unsafe template name: ../x"),
        ):
            with self.subTest(arguments=arguments):
                result = self.scratch.run(*arguments)
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stderr)
                self.assertNotIn("run ", result.stdout)


if __name__ == "__main__":
    unittest.main()
