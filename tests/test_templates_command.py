"""`templates` against the fake engine: the image gate and the one-off
`compose run` of the service it issues."""

from __future__ import annotations

import unittest

from latentcrate import env, templates
from tests.support import Scratch

NO_IMAGE = {"FAKE_ENGINE_IMAGE_MISSING": "true"}


class TemplatesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Scratch()
        self.addCleanup(self.scratch.close)

    def test_a_missing_image_is_refused_before_any_compose_call(self) -> None:
        for arguments in (("templates", "list"), ("templates", "create-model-set", "video_wan")):
            with self.subTest(arguments=arguments):
                result = self.scratch.run(*arguments, **NO_IMAGE)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("latentcrate/comfy:release-", result.stderr)
                self.assertIn(" is not built; run: bash bin/latentcrate build", result.stderr)
                self.assertNotIn("compose ", result.stdout)

    def test_list_runs_the_script_in_a_one_off_release_container_without_the_gpu(self) -> None:
        # The fake engine exits 1 when HF_TOKEN reaches it; the token in the shell must not.
        result = self.scratch.run("templates", "list", COMFY_FRONTEND_MODE="dist", HF_TOKEN="hf_fake_token_for_tests")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        pinned = env.load_pins(self.scratch.root / "pins.env")
        image = f"latentcrate/comfy:release-{pinned['COMFYUI_REF']}"
        self.assertIn(f"compose stdin=devnull image={image} target=runtime env_keys= args=", result.stdout)
        self.assertIn(
            "--env-file pins.env run --rm --no-deps -T --entrypoint python comfy "
            "/usr/local/bin/latentcrate-manage-templates.py list",
            result.stdout,
        )
        self.assertNotIn("compose.gpu.yaml", result.stdout)
        self.assertNotIn("compose.frontend-dist.yaml", result.stdout)
        self.assertNotIn("hf_fake_token_for_tests", result.stdout + result.stderr)

    def test_create_model_set_mounts_the_drafts_directory_and_passes_the_names(self) -> None:
        drafts = self.scratch.root / "build" / templates.DRAFTS_DIR
        result = self.scratch.run("templates", "create-model-set", "video_wan", "--name", "wan-draft")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(drafts.is_dir())
        self.assertIn(
            f"run --rm --no-deps -T --volume {drafts}:/output --entrypoint python comfy "
            "/usr/local/bin/latentcrate-manage-templates.py create-model-set video_wan --name wan-draft",
            result.stdout,
        )
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
                self.assertNotIn("compose ", result.stdout)


if __name__ == "__main__":
    unittest.main()
