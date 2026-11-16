"""The promises a user can observe from bin/latentcrate without a container
engine, routed through the fake engine in tests/fixtures/engine-bin."""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import signal
import subprocess
import sys
import unittest
from unittest import mock

from latentcrate import LatentCrateError, checkpins, cli, env
from tests.support import SOURCE_ROOT, Scratch

USAGE_LINES = (
    "init",
    "doctor [--allow-no-gpu] [--sage mode]",
    "up [--sage mode] [--detach] [--timeout seconds] [--model-set name]... "
    "[--frontend-dist dir | --frontend-source dir]",
    "up --frontend-git <https-url> <ref> [--sage mode] [--detach] [--timeout seconds] [--model-set name]...",
    "build [--sage mode]",
    "build --frontend-git <https-url> <ref> [--sage mode]",
    "down",
    "status",
    "logs",
    "smoke-gpu [--sage mode]",
    "models list",
    "models fetch <set> [<set> ...] | all",
    "models status [--verify] <set> [<set> ...] | all",
    "models check [--remote] <set> [<set> ...] | all",
    "models draft hf:<owner>/<repo>:<file> | civitai:<version>",
    "nodes list",
    "nodes install <set>",
    "nodes sync <set>",
    "nodes status <set>",
    "nodes check <set>",
    "nodes export <custom-nodes-dir>",
    "workflows list",
    "workflows check <set> [<set> ...] | all",
    "templates list",
    "templates create-model-set <template> [--name name]",
    "pins",
    "pins check [component|all]",
)
REMOVED = ("config", "shell", "wait", "node-deps", "versions", "frontend pin-release", "--use-saved-node-deps")
NO_ENGINE = {"PATH": "/usr/bin:/bin"}


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Scratch()
        self.addCleanup(self.scratch.close)

    def expect_failure(self, expected: str, *arguments: str, **overrides: str) -> None:
        result = self.scratch.run(*arguments, **overrides)
        self.assertNotEqual(result.returncode, 0, f"unexpectedly succeeded: {arguments}\n{result.stdout}")
        self.assertIn(expected, result.stdout + result.stderr, f"{arguments}\n{result.stdout}{result.stderr}")

    def succeed(self, *arguments: str, **overrides: str) -> str:
        result = self.scratch.run(*arguments, **overrides)
        self.assertEqual(result.returncode, 0, f"{arguments} failed:\n{result.stdout}{result.stderr}")
        return result.stdout + result.stderr

    def doctor(self, *arguments: str, **overrides: str) -> subprocess.CompletedProcess:
        """`doctor` on the fake Docker engine with a scratch storage root and no CDI spec."""
        cdi = self.scratch.tmp / "cdi"
        return self.scratch.run(
            "doctor",
            *arguments,
            FAKE_ENGINE_STORAGE_ROOT=str(self.scratch.tmp),
            FAKE_DOCKER_CDI_DIRS=str(cdi),
            **overrides,
        )

    def test_help_lists_every_command_and_none_of_the_removed_ones(self) -> None:
        for arguments in ((), ("help",), ("--help",), ("-h",)):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.succeed(*arguments, **NO_ENGINE), cli.USAGE)
        for line in USAGE_LINES:
            self.assertIn(f"\n  bin/latentcrate {line}\n", cli.USAGE)
        self.assertEqual(len(USAGE_LINES), len(cli.COMMANDS))
        for name in REMOVED:
            self.assertNotIn(name, cli.USAGE)
        self.assertIn("Sage modes (LATENTCRATE_SAGE in .env, or --sage for one command):", cli.USAGE)

    def test_pins_prints_the_validated_pin_file_without_the_engine(self) -> None:
        pinned = [
            line for line in (SOURCE_ROOT / "pins.env").read_text().splitlines() if line and not line.startswith("#")
        ]
        self.assertEqual(self.succeed("pins", **NO_ENGINE).splitlines(), pinned)
        pins = self.scratch.root / "pins.env"
        pins.write_text(pins.read_text().replace("+cu130\n", "+cu128\n"))
        self.expect_failure(env.FAMILY_RULE, "pins", **NO_ENGINE)

    def test_pins_check_prints_the_report_before_a_failed_lookup_is_raised(self) -> None:
        failure = checkpins.LookupsFailed("component  pinned  latest\nx  1  (unreadable)\n", ["x"])
        output = io.StringIO()
        with (
            mock.patch.object(checkpins, "check", side_effect=failure) as check,
            contextlib.redirect_stdout(output),
            self.assertRaises(LatentCrateError) as raised,
        ):
            cli.cmd_pins_check(cli.Context(SOURCE_ROOT), {}, ["x"])
        self.assertIs(raised.exception, failure)
        self.assertEqual(output.getvalue(), failure.report)
        self.assertEqual(check.call_args.args[0], "x")
        self.assertEqual(
            check.call_args.args[1]["CUDA_MIN_DRIVER_MAJOR"],
            env.load_pins(SOURCE_ROOT / "pins.env")["CUDA_MIN_DRIVER_MAJOR"],
        )

    def test_pins_and_help_need_neither_the_settings_nor_each_other(self) -> None:
        env_file = self.scratch.root / ".env"
        env_file.write_text("COMFY_PORT=4321\n")
        os.chmod(env_file, 0o644)
        self.assertIn("COMFYUI_REF=", self.succeed("pins", **NO_ENGINE))
        self.expect_failure(f"run: chmod 600 {env_file}", "doctor", "--allow-no-gpu", **NO_ENGINE)
        pins = self.scratch.root / "pins.env"
        pins.unlink()
        self.assertEqual(self.succeed("help", **NO_ENGINE), cli.USAGE)
        self.expect_failure(f"pins file is missing: {pins}", "pins", **NO_ENGINE)

    def test_a_closed_pipe_ends_a_listing_without_a_traceback(self) -> None:
        # A listing fits the pipe buffer, so a reader that is gone (`| head`)
        # is first seen by the final flush; left to interpreter shutdown it
        # is an "Exception ignored" line and exit status 120.
        for command in (["pins"], ["help"]):
            with self.subTest(command=command):
                read_end, write_end = os.pipe()
                os.close(read_end)
                result = subprocess.run(
                    [sys.executable, "-m", "latentcrate", *command],
                    cwd=self.scratch.root,
                    env=self.scratch.environment(**NO_ENGINE),
                    stdout=write_end,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                os.close(write_end)
                self.assertEqual((result.returncode, result.stderr), (1, ""))

    def test_a_closed_or_full_standard_output_is_one_refusal_line(self) -> None:
        closed = subprocess.run(
            ["bash", "-c", 'exec "$1" -m latentcrate pins >&-', "_", sys.executable],
            cwd=self.scratch.root,
            env=self.scratch.environment(**NO_ENGINE),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(closed.returncode, 1, closed.stderr)
        self.assertEqual(len(closed.stderr.splitlines()), 1, closed.stderr)
        self.assertNotIn("Traceback", closed.stderr)
        with open(os.devnull, "rb") as devnull, open("/dev/full", "wb") as full:
            result = subprocess.run(
                [sys.executable, "-m", "latentcrate", "pins"],
                cwd=self.scratch.root,
                env=self.scratch.environment(**NO_ENGINE),
                stdin=devnull,
                stdout=full,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("Exception ignored", result.stderr)

    def test_a_keyboard_interrupt_in_a_handler_exits_130(self) -> None:
        with (
            mock.patch.object(cli, "install_signal_handlers"),
            mock.patch.object(cli, "dispatch", side_effect=KeyboardInterrupt),
        ):
            self.assertEqual(cli.main(["status"]), 130)

    def test_init_creates_the_directories_and_a_private_comment_only_env_file(self) -> None:
        fresh = self.scratch.tmp / "fresh"
        settings = {
            "COMFY_DATA_DIR": str(fresh / "data"),
            "COMFY_MODELS_DIR": str(fresh / "models"),
            "COMFY_CACHE_DIR": str(fresh / "cache"),
            "COMFY_LOCAL_NODES_DIR": str(fresh / "local-nodes"),
        }
        env_file = self.scratch.root / ".env"
        output = self.succeed("init", **NO_ENGINE, **settings)
        self.assertIn(f"Created {env_file}\n", output)
        self.assertIn("LatentCrate host directories are ready. Review .env before building.\n", output)
        for name in ("data", "data/custom_nodes", "models", "cache", "local-nodes"):
            self.assertTrue((fresh / name).is_dir(), name)
        self.assertTrue((self.scratch.root / "local" / "model-sets").is_dir())
        self.assertTrue((self.scratch.root / "local" / "workflow-sets" / "workflows").is_dir())
        self.assertEqual(oct(env_file.stat().st_mode & 0o777), "0o600")
        lines = env_file.read_text().splitlines()
        self.assertTrue(lines and all(line.startswith("#") for line in lines), lines)
        self.assertIn("HF_TOKEN", env_file.read_text())
        # A second run keeps the file; one the user widened is refused, with the remedy.
        env_file.write_text("COMFY_PORT=4321\n")
        os.chmod(env_file, 0o600)
        self.assertIn(f"Keeping existing {env_file}\n", self.succeed("init", **NO_ENGINE, **settings))
        self.assertEqual(env_file.read_text(), "COMFY_PORT=4321\n")
        os.chmod(env_file, 0o644)
        self.expect_failure(f"run: chmod 600 {env_file}", "init", **NO_ENGINE, **settings)

    def test_init_refuses_a_storage_path_that_is_a_file_before_creating_anything(self) -> None:
        fresh = self.scratch.tmp / "fresh"
        fresh.mkdir()
        (fresh / "cache").write_text("")
        settings = {
            "COMFY_DATA_DIR": str(fresh / "data"),
            "COMFY_MODELS_DIR": str(fresh / "models"),
            "COMFY_CACHE_DIR": str(fresh / "cache"),
            "COMFY_LOCAL_NODES_DIR": str(fresh / "local-nodes"),
        }
        self.expect_failure(f"COMFY_CACHE_DIR is not a directory: {fresh / 'cache'}", "init", **NO_ENGINE, **settings)
        self.assertEqual(sorted(path.name for path in fresh.iterdir()), ["cache"])
        (fresh / "cache").unlink()
        (fresh / "data").mkdir()
        (fresh / "data" / "custom_nodes").write_text("")
        self.expect_failure(
            f"the managed custom-node directory is not a directory: {fresh / 'data' / 'custom_nodes'}",
            "init",
            **NO_ENGINE,
            **settings,
        )
        self.assertFalse((fresh / "models").exists())

    def test_models_list_marks_a_private_set_and_refuses_a_name_a_shipped_set_holds(self) -> None:
        shipped = self.scratch.root / "config" / "model-sets" / "minimax-h3-i2v.toml"
        private = self.scratch.root / "local" / "model-sets"
        private.mkdir(parents=True)
        body = shipped.read_text().replace('description = "', 'description = "Private ', 1)
        (private / "mine.toml").write_text(body)
        listing = self.succeed("models", "list", **NO_ENGINE)
        self.assertRegex(listing, r"\nminimax-h3-i2v - [^(\n]+\n")
        self.assertRegex(listing, r"\nmine - Private .+ \(local\)\n$")

        (private / "minimax-h3-i2v.toml").write_text(body)
        self.expect_failure(
            f"model set minimax-h3-i2v is declared twice: {shipped} and {private / 'minimax-h3-i2v.toml'}",
            "models",
            "list",
            **NO_ENGINE,
        )

    def test_workflows_list_and_check_cover_the_private_sets_and_refuse_a_shipped_name(self) -> None:
        # No set ships yet, so the shipped ones come from the fixtures too.
        fixtures = SOURCE_ROOT / "tests" / "fixtures" / "workflow-sets"
        shipped = self.scratch.root / "config" / "workflow-sets"
        private = self.scratch.root / "local" / "workflow-sets"
        shutil.copy2(fixtures / "resolved.toml", shipped)
        shutil.copytree(fixtures / "workflows" / "resolved", shipped / "workflows" / "resolved")
        private.mkdir(parents=True)
        shutil.copy2(fixtures / "broken.toml", private)
        shutil.copytree(fixtures / "workflows" / "broken", private / "workflows" / "broken")
        self.assertEqual(
            self.succeed("workflows", "list", **NO_ENGINE),
            "resolved - Fixture: text to image, and an upscale inside a subgraph\n"
            "broken - Fixture: a workflow whose model, node pack, and node no set provides (local)\n",
        )
        result = self.scratch.run("workflows", "check", "broken", **NO_ENGINE)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("broken/missing.json: model absent.safetensors: no model set provides it\n", result.stdout)
        # Private model sets provide models too.
        commit = "a" * 40
        (self.scratch.root / "local" / "model-sets").mkdir(parents=True)
        (self.scratch.root / "local" / "model-sets" / "mine.toml").write_text(
            f"""schema = 1
description = "Private"
workflow_urls = ["https://github.com/Comfy-Org/workflow_templates/blob/{commit}/templates/x.json"]
[[license]]
name = "Test"
url = "https://huggingface.co/example/model/blob/{commit}/LICENSE"
[[file]]
repository = "example/model"
revision = "{commit}"
source = "gone.safetensors"
destination = "loras/gone.safetensors"
"""
        )
        result = self.scratch.run("workflows", "check", "broken", **NO_ENGINE)
        self.assertIn("broken/missing.json: model loras/gone.safetensors: mine (local)\n", result.stdout)
        self.expect_failure("unknown workflow set: nope (available: resolved, broken)", "workflows", "check", "nope")

        shutil.copy2(fixtures / "resolved.toml", private)
        shutil.copytree(fixtures / "workflows" / "resolved", private / "workflows" / "resolved")
        self.expect_failure(
            f"workflow set resolved is declared twice: {shipped / 'resolved.toml'} and {private / 'resolved.toml'}",
            "workflows",
            "list",
            **NO_ENGINE,
        )

    def test_doctor_runs_on_the_fake_engine_and_needs_a_gpu_unless_told_otherwise(self) -> None:
        allowed = self.doctor("--allow-no-gpu")
        self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
        self.assertIn("== LatentCrate host doctor ==\n", allowed.stdout)
        self.assertIn("[ok] docker engine is reachable\n", allowed.stdout)
        self.assertIn("(--allow-no-gpu was supplied)", allowed.stdout)
        self.assertRegex(allowed.stdout, r"Doctor completed with 0 failure\(s\) and [1-9][0-9]* warning\(s\)\.")
        refused = self.doctor()
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertRegex(refused.stdout, r"\[fail\] .*GPU")
        sage = self.doctor("--allow-no-gpu", "--sage", "Available")
        self.assertEqual(sage.returncode, 0, sage.stdout + sage.stderr)
        self.assertIn("[ok] checking the SageAttention-capable image\n", sage.stdout)
        self.expect_failure("--sage requires a mode: off, available, or global", "doctor", "--sage", "fast")
        self.expect_failure("--sage requires a mode: off, available, or global", "doctor", "--sage")

    def test_the_tokens_in_the_shell_never_reach_the_engine(self) -> None:
        # The fake engine exits 1 when it sees a token; the probes must still succeed.
        result = self.doctor("--allow-no-gpu", HF_TOKEN="hf_secret", CIVITAI_TOKEN="civitai_secret")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[ok] docker engine is reachable\n", result.stdout)
        self.assertNotIn("hf_secret", result.stdout + result.stderr)
        self.assertNotIn("civitai_secret", result.stdout + result.stderr)

    def test_the_shim_runs_the_package(self) -> None:
        for arguments in (("help",), ("pins",)):
            with self.subTest(arguments=arguments):
                shim, direct = self.scratch.run_shim(*arguments, **NO_ENGINE), self.scratch.run(*arguments, **NO_ENGINE)
                self.assertEqual((shim.returncode, shim.stdout, shim.stderr), (0, direct.stdout, ""))

    def test_the_shim_runs_the_package_through_a_symlink_from_another_directory(self) -> None:
        link = self.scratch.tmp / "elsewhere" / "lc"
        link.parent.mkdir()
        link.symlink_to(self.scratch.root / "bin" / "latentcrate")
        result = subprocess.run(
            [str(link), "help"],
            cwd=link.parent,
            env=self.scratch.environment(PYTHONPATH=None, **NO_ENGINE),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, cli.USAGE, ""))

    def test_every_command_family_reaches_its_module(self) -> None:
        # Each family once, cheaply: a listing, a quiet no-op against the
        # fake engine, or the refusal its module words.
        self.assertEqual(self.succeed("status"), "")
        self.assertEqual(self.succeed("down"), "")
        self.expect_failure("the ComfyUI container does not exist; run: bash bin/latentcrate up", "logs")
        self.assertIn("args=logs --follow fake-comfy-container", self.succeed("logs", FAKE_ENGINE_COMFY_EXITED="true"))
        self.assertRegex(self.succeed("models", "list", **NO_ENGINE), r"\nminimax-h3-i2v - .+\n")
        self.assertIn("latent-nodepack - ", self.succeed("nodes", "list", **NO_ENGINE))
        self.assertRegex(self.succeed("nodes", "check", "latent-nodepack", **NO_ENGINE), r"\n\S+ [0-9a-f]{40} https://")
        empty = self.scratch.tmp / "custom_nodes"
        empty.mkdir()
        self.expect_failure(
            f"no clean public node checkouts could be exported from {empty}", "nodes", "export", str(empty)
        )
        self.expect_failure(
            "is not built; run: bash bin/latentcrate build", "templates", "list", FAKE_ENGINE_IMAGE_MISSING="true"
        )
        self.expect_failure(
            "use individual --model-set names with up; all is available through models fetch",
            "up",
            "--model-set",
            "all",
            **NO_ENGINE,
        )
        self.expect_failure("unknown model set: nope", "up", "--model-set", "nope", **NO_ENGINE)
        missing = self.scratch.tmp / "no-dist"
        self.expect_failure(
            f"frontend dist/index.html is missing or unreadable: {missing}",
            "up",
            "--frontend-dist",
            str(missing),
            **NO_ENGINE,
        )
        self.expect_failure(
            "build supports the pinned and Git frontends; use up for --frontend-source or --frontend-dist",
            "build",
            COMFY_FRONTEND_MODE="dist",
            COMFY_FRONTEND_DIST_DIR=str(SOURCE_ROOT / "tests" / "fixtures" / "frontend-dist"),
            **NO_ENGINE,
        )
        self.expect_failure(
            "fix the [fail] lines above before starting (bash bin/latentcrate doctor repeats them)", "up"
        )

    def test_option_errors(self) -> None:
        self.expect_failure("unexpected argument for down: extra", "down", "extra")
        self.expect_failure("--timeout requires a positive integer", "up", "--timeout", "nope")
        self.expect_failure("--detach is only valid with up", "build", "--detach")
        self.expect_failure("--allow-no-gpu is only valid with doctor", "up", "--allow-no-gpu")
        self.expect_failure("--verify applies to models status", "models", "fetch", "--verify", "a")
        self.expect_failure("unknown option for up: --x", "up", "--x")
        self.expect_failure("--sage may be given once with doctor", "doctor", "--sage", "off", "--sage", "global")
        self.expect_failure(
            "--frontend-dist and --frontend-source may not be combined with up",
            "up",
            "--frontend-dist",
            "d",
            "--frontend-source",
            "e",
        )
        self.expect_failure("--model-set requires a model set name", "up", "--model-set", "../x")
        self.expect_failure(
            "usage: bin/latentcrate up --frontend-git <https-url> <ref>", "up", "--frontend-git", "https://x"
        )
        self.expect_failure("usage: bin/latentcrate pins check [component|all]", "pins", "check", "node", "extra")
        self.expect_failure("usage: bin/latentcrate models list | fetch | status | check | draft", "models", "bogus")
        self.expect_failure("usage: bin/latentcrate nodes install <set>", "nodes", "install")
        for name in ("wait", "config", "shell", "frobnicate"):
            result = self.scratch.run(name)
            self.assertEqual(result.returncode, 1)
            self.assertIn(f"unknown command: {name}", result.stderr)
            self.assertIn("Usage:", result.stderr)

    def test_every_usage_line_names_exactly_the_flags_its_entry_accepts(self) -> None:
        for command in cli.COMMANDS:
            with self.subTest(command=command.name):
                self.assertIn(f"  {command.usage}\n      {command.summary}\n", cli.USAGE)
                self.assertEqual(set(re.findall(r"--[a-z][a-z-]*", command.usage)), set(command.flags))

    def test_argv_resolves_to_the_subcommand_or_mode_it_names(self) -> None:
        for argv, expected in (
            (["up"], "up"),
            (["up", "--sage", "off", "--frontend-git", "https://x", "main"], "up --frontend-git"),
            (["build", "--frontend-git", "https://x", "v1"], "build --frontend-git"),
            (["models", "check", "--remote", "a"], "models check"),
            (["pins"], "pins"),
            (["pins", "check", "comfyui"], "pins check"),
        ):
            self.assertEqual(cli.resolve(argv)[0].name, expected, argv)

    def test_every_flag_is_refused_by_every_entry_that_does_not_take_it(self) -> None:
        flags = {flag: spec for command in cli.COMMANDS for flag, spec in command.flags.items()}
        for command in cli.COMMANDS:
            for flag, spec in sorted(flags.items()):
                with self.subTest(command=command.name, flag=flag):
                    value = ["off" if spec is cli.SAGE else "1"] if spec.what else []
                    arguments = ["a"] * command.arguments[0] + [flag, *value]
                    if flag in command.flags:
                        self.assertIn(flag, cli.parse(command, arguments)[0])
                        continue
                    with self.assertRaises(LatentCrateError) as refused:
                        cli.parse(command, arguments)
                    self.assertRegex(
                        str(refused.exception),
                        rf"^{re.escape(flag)} (is only valid with|applies to|is not valid with) ",
                    )

    def test_the_refusals_the_documentation_quotes_are_generated_unchanged(self) -> None:
        by_name = cli.BY_NAME
        for name, flag, expected in (
            ("build", "--detach", "--detach is only valid with up"),
            ("build", "--frontend-dist", "--frontend-dist is only valid with up"),
            ("up", "--allow-no-gpu", "--allow-no-gpu is only valid with doctor"),
            ("up", "--verify", "--verify is only valid with models status"),
            ("up", "--remote", "--remote is only valid with models check"),
            ("models fetch", "--verify", "--verify applies to models status"),
            ("up --frontend-git", "--frontend-dist", "--frontend-dist is not valid with --frontend-git"),
            ("init", "--sage", "--sage is only valid with doctor, up, build, or smoke-gpu"),
            ("up", "--bogus", "unknown option for up: --bogus"),
        ):
            self.assertEqual(cli.refusal(by_name[name], flag), expected)
        options, names = cli.parse(by_name["up"], ["--model-set", "a", "--detach", "--model-set", "b"])
        self.assertEqual((options, names), ({"--model-set": ["a", "b"], "--detach": True}, []))
        options, names = cli.parse(
            by_name["up --frontend-git"], ["--frontend-git", "https://x", "main", "--sage", "Global"]
        )
        self.assertEqual((options, names), ({"--frontend-git": True, "--sage": "Global"}, ["https://x", "main"]))


class SignalTests(unittest.TestCase):
    def test_a_hang_up_or_termination_unwinds_and_an_inherited_ignore_is_left_alone(self) -> None:
        # nohup and setsid ignore SIGHUP; a backgrounded command keeps running.
        previous = {signum: signal.getsignal(signum) for signum in (signal.SIGHUP, signal.SIGTERM)}
        self.addCleanup(lambda: [signal.signal(signum, handler) for signum, handler in previous.items()])
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        cli.install_signal_handlers()
        self.assertIs(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)
        with self.assertRaises(SystemExit) as exited:
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        self.assertEqual(exited.exception.code, 128 + signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
