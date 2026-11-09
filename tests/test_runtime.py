"""The service lifecycle against the fake engine: the requirement-file
copy, build, start with its readiness wait, stop, logs, and the GPU
smoke test."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from latentcrate import LatentCrateError, env, runtime, variants
from latentcrate import engine as engines
from latentcrate.engine import Engine
from tests.support import SOURCE_ROOT, Scratch, serving

FIXTURE_NODE = SOURCE_ROOT / "tests" / "fixtures" / "custom-nodes" / "Example"
# A process that holds the project lock under the named command until its stdin closes.
HOLDER = textwrap.dedent(
    """
    import os, sys
    from pathlib import Path
    from latentcrate import engine, env
    settings = env.load(Path(sys.argv[1]), dict(os.environ))
    with engine.Engine("docker", settings).lock(sys.argv[2]):
        print("held", flush=True)
        sys.stdin.readline()
    """
)


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Scratch()
        self.addCleanup(self.scratch.close)
        self.managed = self.scratch.storage / "data" / "custom_nodes"
        self.local = self.scratch.storage / "local-nodes"
        self.managed.mkdir()

    def settings(self, **overrides: str) -> env.Settings:
        return env.load(self.scratch.root, self.scratch.environment(**overrides))

    def pinned(self) -> dict[str, str]:
        return env.load_pins(self.scratch.root / "pins.env")

    def variant(self, settings: env.Settings, sage: str | None = None) -> variants.Variant:
        return variants.select(settings, self.pinned(), sage=sage)

    def traced(self, engine: Engine) -> list[str]:
        """Record every Compose and engine invocation's trace line, with the
        fake engine's stdout captured instead of printed."""
        traces: list[str] = []
        compose, command = engine.compose, engine.command

        def capture(call, *args, **kwargs):
            kwargs["capture"] = True
            result = call(*args, **kwargs)
            traces.extend(result.stdout.splitlines())
            return result

        engine.compose = lambda *args, **kwargs: capture(compose, *args, **kwargs)
        engine.command = lambda *args, **kwargs: capture(command, *args, **kwargs)
        return traces

    def holder(self, command: str) -> subprocess.Popen:
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(self.scratch.root), command],
            cwd=self.scratch.root,
            env=self.scratch.environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(holder.stdout.readline(), "held\n")
        self.addCleanup(holder.stdout.close)
        return holder

    # --- The requirement-file copy -------------------------------------------

    def test_requirement_files_and_their_includes_are_copied_with_a_manifest(self) -> None:
        shutil.copytree(FIXTURE_NODE, self.managed / "Example")
        (self.managed / "bare-node").mkdir()
        (self.managed / "old.disabled").mkdir()
        (self.managed / "old.disabled" / "requirements.txt").write_text("x\n")
        (self.local / "a-local").mkdir()
        (self.local / "a-local" / "requirements.txt").write_text("numpy\n")
        (self.local / ".gitkeep").touch()
        runtime.copy_requirements(self.settings())
        tree = self.scratch.root / "build" / "custom-node-requirements"
        self.assertEqual((tree / "manifest.txt").read_text(), "a-local/requirements.txt\nExample/requirements.txt\n")
        self.assertEqual((tree / "Example" / "deps" / "base.txt").read_text(), "packaging==25.0\n")
        self.assertEqual((tree / "Example" / "requirements.txt").read_text(), "-r deps/base.txt\n")
        self.assertEqual((tree / "a-local" / "requirements.txt").read_text(), "numpy\n")
        self.assertTrue((tree / ".gitkeep").is_file())
        self.assertFalse((tree / "old.disabled").exists())
        self.assertEqual(sorted(path.name for path in self.scratch.root.glob("build/.custom-node-requirements*")), [])
        # A second copy replaces the tree and nothing of the first remains.
        shutil.rmtree(self.managed / "Example")
        runtime.copy_requirements(self.settings())
        self.assertEqual((tree / "manifest.txt").read_text(), "a-local/requirements.txt\n")
        self.assertFalse((tree / "Example").exists())

    def test_the_managed_root_is_created_and_a_missing_local_root_is_refused(self) -> None:
        shutil.rmtree(self.managed)
        runtime.copy_requirements(self.settings())
        self.assertTrue(self.managed.is_dir())
        shutil.rmtree(self.local)
        with self.assertRaisesRegex(LatentCrateError, "local custom-node directory does not exist.*latentcrate init"):
            runtime.copy_requirements(self.settings())

    def test_symlinked_hidden_and_duplicate_nodes_are_refused(self) -> None:
        (self.managed / "linked").symlink_to(FIXTURE_NODE)
        with self.assertRaisesRegex(LatentCrateError, "top-level custom-node symlinks are not allowed: .*linked"):
            runtime.copy_requirements(self.settings())
        (self.managed / "linked").unlink()
        (self.managed / ".hidden").mkdir()
        with self.assertRaisesRegex(LatentCrateError, "hidden custom-node directories are not supported: .*hidden"):
            runtime.copy_requirements(self.settings())
        (self.managed / ".hidden").rmdir()
        (self.managed / "SharedNode").mkdir()
        (self.local / "sharednode").mkdir()
        with self.assertRaisesRegex(
            LatentCrateError, "duplicate custom-node name in managed and local sources: SharedNode / sharednode"
        ):
            runtime.copy_requirements(self.settings())
        self.assertFalse((self.scratch.root / "build" / "custom-node-requirements").exists())
        self.assertEqual(list(self.scratch.root.glob("build/.custom-node-requirements*")), [])

    def test_an_include_must_stay_inside_the_node_without_symbolic_links(self) -> None:
        node = self.managed / "node"
        node.mkdir()
        outside = self.scratch.tmp / "outside.txt"
        outside.write_text("evil\n")
        (node / "requirements.txt").write_text("-r ../../outside.txt\n")
        with self.assertRaisesRegex(LatentCrateError, "requirement include escapes its custom-node directory"):
            runtime.copy_requirements(self.settings())
        (node / "requirements.txt").write_text("-c linked.txt\n")
        (node / "linked.txt").symlink_to(outside)
        with self.assertRaisesRegex(LatentCrateError, "requirement include escapes its custom-node directory"):
            runtime.copy_requirements(self.settings())
        (node / "requirements.txt").write_text("--requirement=missing.txt\n")
        with self.assertRaisesRegex(LatentCrateError, "referenced requirement file does not exist: .*missing.txt"):
            runtime.copy_requirements(self.settings())

    def test_a_failed_swap_restores_the_previous_tree(self) -> None:
        shutil.copytree(FIXTURE_NODE, self.managed / "Example")
        settings = self.settings()
        runtime.copy_requirements(settings)
        tree = self.scratch.root / "build" / "custom-node-requirements"
        before = (tree / "manifest.txt").read_text()
        shutil.rmtree(self.managed / "Example")
        renames: list[str] = []
        rename = os.rename

        def failing_second_rename(source, target):
            renames.append(Path(source).name)
            if len(renames) == 2:
                raise OSError("simulated rename failure")
            rename(source, target)

        with mock.patch.object(runtime.os, "rename", failing_second_rename), self.assertRaises(OSError):
            runtime.copy_requirements(settings)
        self.assertEqual(len(renames), 3)
        self.assertEqual((tree / "manifest.txt").read_text(), before)
        self.assertEqual(list(self.scratch.root.glob("build/.custom-node-requirements*")), [])

    def test_a_reader_never_sees_a_half_tree(self) -> None:
        shutil.copytree(FIXTURE_NODE, self.managed / "Example")
        settings = self.settings()
        runtime.copy_requirements(settings)
        tree = self.scratch.root / "build" / "custom-node-requirements"
        stop = threading.Event()
        listings: list[set[str]] = []

        def read() -> None:
            while not stop.is_set():
                try:
                    listings.append(set(os.listdir(tree)))
                except FileNotFoundError:
                    continue

        reader = threading.Thread(target=read)
        reader.start()
        try:
            for _ in range(20):
                runtime.copy_requirements(settings)
        finally:
            stop.set()
            reader.join()
        self.assertTrue(listings)
        for listing in listings:
            self.assertLessEqual({"manifest.txt", ".gitkeep", "Example"}, listing)

    # --- Build and start -----------------------------------------------------------

    def test_build_holds_the_lock_and_renders_the_variant(self) -> None:
        settings = self.settings(HF_TOKEN="secret-fixture")
        engine = Engine("docker", settings)
        traces = self.traced(engine)
        compose = engine.compose
        held: list[str] = []

        def record(*args, **kwargs):
            held.append(engine.lock_file.read_text())
            return compose(*args, **kwargs)

        engine.compose = record
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            runtime.build_image(settings, engine, self.variant(settings), self.pinned())
        self.assertEqual(held, [f"{os.getpid()} build\n"])
        self.assertIn("== Building the ComfyUI image ==", printed.getvalue())
        self.assertEqual(len(traces), 1)
        self.assertIn(
            f"image=latentcrate/comfy:release-{self.pinned()['COMFYUI_REF']} target=runtime env_keys= "
            "args=compose --file compose.yaml "
            "--file compose.docker.yaml --file compose.gpu.yaml --env-file pins.env build comfy",
            traces[0],
        )
        self.assertTrue((self.scratch.root / "build" / "custom-node-requirements" / "manifest.txt").is_file())

    def test_up_builds_starts_detached_records_the_stack_and_waits_for_readiness(self) -> None:
        settings = self.settings(FAKE_ENGINE_COMFY_RUNNING="true")
        engine = Engine("docker", settings)
        traces = self.traced(engine)
        with serving(int(self.scratch.port)), contextlib.redirect_stdout(io.StringIO()) as printed:
            status = runtime.start_stack(
                settings, engine, self.variant(settings), self.pinned(), timeout=5, detach=True
            )
        self.assertEqual(status, 0)
        self.assertEqual(
            [line.split(" args=")[1] for line in traces if line.startswith("compose")][:2],
            [
                "compose --file compose.yaml --file compose.docker.yaml --file compose.gpu.yaml --env-file pins.env "
                "build comfy",
                "compose --file compose.yaml --file compose.docker.yaml --file compose.gpu.yaml --env-file pins.env "
                "up --no-build --force-recreate --detach comfy",
            ],
        )
        self.assertIn("compose stdin=devnull", [line for line in traces if "up --no-build" in line][0])
        self.assertNotIn("logs", "\n".join(traces))
        self.assertEqual(
            json.loads((self.scratch.root / "build" / "stack.json").read_text()),
            {"engine": "docker", "project": "latentcrate"},
        )
        self.assertEqual(printed.getvalue().splitlines()[-1], f"LatentCrate is healthy at {settings.service_url}")

    def test_a_foreground_up_follows_the_logs_and_ctrl_c_stops_the_container(self) -> None:
        settings = self.settings(FAKE_ENGINE_COMFY_RUNNING="true", FAKE_ENGINE_LOGS_INTERRUPT="true")
        engine = Engine("docker", settings)
        traces = self.traced(engine)
        with serving(int(self.scratch.port)), contextlib.redirect_stdout(io.StringIO()):
            status = runtime.start_stack(
                settings, engine, self.variant(settings), self.pinned(), timeout=5, detach=False
            )
        self.assertEqual(status, 130)
        tail = [line.split(" args=")[1] for line in traces if line.startswith(("logs", "stop"))]
        self.assertEqual(tail, ["logs --follow fake-comfy-container", "stop --time 30 fake-comfy-container"])
        # A follow that ends by itself returns its status without a stop.
        settings = self.settings(FAKE_ENGINE_COMFY_RUNNING="true")
        engine = Engine("docker", settings)
        traces = self.traced(engine)
        with serving(int(self.scratch.port)), contextlib.redirect_stdout(io.StringIO()):
            status = runtime.start_stack(
                settings, engine, self.variant(settings), self.pinned(), timeout=5, detach=False
            )
        self.assertEqual(status, 0)
        self.assertNotIn("stop", "\n".join(traces))
        self.assertEqual(json.loads(engine.stack_record.read_text())["engine"], "docker")

    def test_ctrl_c_during_the_readiness_wait_stops_the_container(self) -> None:
        for detach in (False, True):
            settings = self.settings(FAKE_ENGINE_COMFY_RUNNING="true")
            engine = Engine("docker", settings)
            traces = self.traced(engine)
            with (
                self.subTest(detach=detach),
                mock.patch.object(runtime, "wait_until_ready", side_effect=KeyboardInterrupt),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                status = runtime.start_stack(
                    settings, engine, self.variant(settings), self.pinned(), timeout=5, detach=detach
                )
                self.assertEqual(status, 130)
                self.assertEqual(traces[-1].split(" args=")[1], "stop --time 30 fake-comfy-container")
                self.assertNotIn("logs", "\n".join(traces))

    def test_a_keyboard_interrupt_during_the_follow_counts_as_ctrl_c(self) -> None:
        engine = Engine("docker", self.settings(FAKE_ENGINE_COMFY_RUNNING="true"))
        with (
            mock.patch.object(runtime, "comfy_container", return_value="fake-comfy-container"),
            mock.patch.object(engine, "command", side_effect=KeyboardInterrupt),
        ):
            self.assertEqual(runtime.follow_logs(engine), 130)

    def test_a_readiness_failure_names_the_logs(self) -> None:
        settings = self.settings(FAKE_ENGINE_COMFY_EXITED="true", FAKE_ENGINE_STATE="exited")
        with self.assertRaisesRegex(
            LatentCrateError, "container is exited before it was ready. Check the logs: bash bin/latentcrate logs"
        ):
            runtime.wait_until_ready(settings, Engine("docker", settings), 5)
        settings = self.settings(FAKE_ENGINE_COMFY_RUNNING="true")
        clock = [0.0]

        def tick(seconds: float) -> None:
            clock[0] += seconds

        with self.assertRaisesRegex(
            LatentCrateError, "container did not become healthy within 7s. Check the logs: bash bin/latentcrate logs"
        ):
            runtime.wait_until_ready(settings, Engine("docker", settings), 7, sleep=tick, clock=lambda: clock[0])
        self.assertGreaterEqual(clock[0], 7)
        # No container at all is judged as removed.
        with self.assertRaisesRegex(LatentCrateError, "container is removed before it was ready"):
            runtime.wait_until_ready(self.settings(), Engine("docker", self.settings()), 5)

    def test_a_second_up_waits_on_the_lock_and_names_its_holder(self) -> None:
        settings = self.settings(FAKE_ENGINE_COMFY_RUNNING="true")
        engine = Engine("docker", settings)
        self.traced(engine)
        holder = self.holder("up")
        release = threading.Timer(0.3, holder.stdin.close)
        with (
            mock.patch.object(engines, "warn") as warn,
            mock.patch.object(runtime, "wait_until_ready"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            release.start()
            runtime.start_stack(settings, engine, self.variant(settings), self.pinned(), timeout=5, detach=True)
        self.assertEqual(holder.wait(5), 0)
        warn.assert_called_once_with(f"waiting for `up` (pid {holder.pid}) to finish")

    def test_up_refuses_a_selected_set_with_a_missing_node(self) -> None:
        sets = self.scratch.root / "config" / "custom-nodes" / "sets"
        name = sorted(sets.glob("*.toml"))[0].stem
        node = re.search(r'^name = "([^"]+)"', (sets / f"{name}.toml").read_text(), re.MULTILINE).group(1)
        (self.managed / node).mkdir()
        (self.managed / ".latentcrate-node-set").write_text(f"{name}\n")
        settings = self.settings()
        engine = Engine("docker", settings)
        traces = self.traced(engine)
        with self.assertRaisesRegex(LatentCrateError, f"custom-node set {name} is missing .*nodes install {name}"):
            runtime.start_stack(settings, engine, self.variant(settings), self.pinned(), timeout=5, detach=True)
        self.assertEqual(traces, [])
        self.assertFalse((self.scratch.root / "build" / "custom-node-requirements").exists())

    # --- Stop, status, logs ------------------------------------------------------------

    def test_down_does_not_wait_for_the_lock(self) -> None:
        settings = self.settings()
        engine = Engine("docker", settings)
        engine.record_stack()
        holder = self.holder("build")
        with mock.patch.object(engines, "warn") as warn, ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(runtime.stop_stack, engine, stop_seconds=30).result(timeout=3)
        warn.assert_not_called()
        self.assertFalse(engine.stack_record.exists())
        holder.stdin.close()
        self.assertEqual(holder.wait(5), 0)

    def test_down_removes_only_this_projects_containers_and_needs_no_pins(self) -> None:
        (self.scratch.root / "pins.env").write_text("not a pin file\n")
        settings = self.settings(FAKE_ENGINE_COMFY_RUNNING="true", FAKE_ENGINE_NETWORK="latentcrate_default")
        engine = Engine("docker", settings)
        traces = self.traced(engine)
        runtime.stop_stack(engine, stop_seconds=45)
        self.assertEqual(
            [line.split(" args=")[1] for line in traces if " args=" in line],
            ["stop --time 45 fake-comfy-container", "rm fake-comfy-container", "network rm latentcrate_default"],
        )
        settings = self.settings(FAKE_ENGINE_COMFY_RUNNING="true", FAKE_ENGINE_PROJECT="other")
        engine = Engine("docker", settings)
        traces = self.traced(engine)
        engine.record_stack()
        runtime.stop_stack(engine, stop_seconds=30)
        self.assertEqual([line for line in traces if " args=" in line], [])
        self.assertFalse(engine.stack_record.exists())
        # Switching engines under a running stack is refused by `engine.detect`
        # (tests/test_engine.py, the stack record test).

    def test_status_lists_the_projects_containers(self) -> None:
        settings = self.settings(FAKE_ENGINE_COMFY_RUNNING="true")
        engine = Engine("docker", settings)
        with mock.patch.object(engine, "command") as command:
            runtime.print_containers(settings, engine)
        command.assert_called_once_with("ps", "--all", "--filter", "label=com.docker.compose.project=latentcrate")

    def test_logs_needs_the_container(self) -> None:
        with self.assertRaisesRegex(
            LatentCrateError, "the ComfyUI container does not exist; run: bash bin/latentcrate up"
        ):
            runtime.follow_logs(Engine("docker", self.settings()))
        engine = Engine("docker", self.settings(FAKE_ENGINE_COMFY_EXITED="true"))
        traces = self.traced(engine)
        self.assertEqual(runtime.follow_logs(engine), 0)
        self.assertIn("logs --follow fake-comfy-container", traces[-1])
        with mock.patch.object(engines.Engine, "container_ids", return_value=["a", "b"]):
            with self.assertRaisesRegex(
                LatentCrateError, "multiple ComfyUI containers .*; run: bash bin/latentcrate down"
            ):
                runtime.follow_logs(engine)

    def test_the_grace_period_is_parsed_as_a_compose_duration(self) -> None:
        self.assertEqual(runtime.grace_seconds(self.settings()), 30)
        self.assertEqual(runtime.grace_seconds(self.settings(STOP_GRACE_PERIOD="1m")), 60)
        self.assertEqual(runtime.grace_seconds(self.settings(STOP_GRACE_PERIOD="2h")), 7200)
        for bad in ("30", "1m30s", "500ms", "", "soon"):
            with self.subTest(bad=bad), self.assertRaisesRegex(LatentCrateError, "STOP_GRACE_PERIOD must be"):
                runtime.grace_seconds(self.settings(STOP_GRACE_PERIOD=bad))

    # --- The GPU smoke test ------------------------------------------------------------

    def test_the_smoke_test_asserts_the_running_image(self) -> None:
        settings = self.settings()
        variant, pinned = self.variant(settings), self.pinned()
        with self.assertRaisesRegex(
            LatentCrateError, "the ComfyUI container is not running; run: bash bin/latentcrate up"
        ):
            runtime.smoke_gpu(settings, Engine("docker", settings), variant, pinned)
        settings = self.settings(FAKE_ENGINE_COMFY_RUNNING="true", FAKE_ENGINE_IMAGE_MISSING="true")
        with self.assertRaisesRegex(LatentCrateError, re.escape(f"does not exist locally: {variant.image(pinned)}")):
            runtime.smoke_gpu(settings, Engine("docker", settings), variant, pinned)
        settings = self.settings(FAKE_ENGINE_COMFY_RUNNING="true", FAKE_ENGINE_OTHER_IMAGE="true")
        with self.assertRaisesRegex(
            LatentCrateError,
            re.escape(f"the running container uses another image than {variant.image(pinned)}; rerun up with the same"),
        ):
            runtime.smoke_gpu(settings, Engine("docker", settings), variant, pinned)
        self.assertFalse((self.scratch.root / "reports").exists())

    def test_the_smoke_report_is_saved_and_requires_sage_only_with_a_sage_variant(self) -> None:
        settings = self.settings(FAKE_ENGINE_COMFY_RUNNING="true")
        engine = Engine("docker", settings)
        pinned = self.pinned()
        for sage, expected in (
            ("off", "exec fake-comfy-container /usr/local/bin/latentcrate-gpu-smoke"),
            (
                "available",
                "exec fake-comfy-container env LATENTCRATE_REQUIRE_SAGE=1 /usr/local/bin/latentcrate-gpu-smoke",
            ),
        ):
            with self.subTest(sage=sage), contextlib.redirect_stdout(io.StringIO()) as printed:
                variant = self.variant(settings, sage=sage)
                self.assertEqual(runtime.smoke_gpu(settings, engine, variant, pinned), 0)
            tag = variant.image(pinned).rpartition(":")[2]
            saved = re.search(r"^Saved GPU report to (.*)$", printed.getvalue(), re.MULTILINE).group(1)
            report = Path(saved)
            self.assertEqual(report.parent, self.scratch.root / "reports")
            self.assertRegex(report.name, rf"^gpu-{re.escape(tag)}-\d{{8}}-\d{{6}}\.log$")
            self.assertTrue(report.read_text().endswith(f"args={expected}\n"))
            self.assertIn(report.read_text().strip(), printed.getvalue())
        settings = self.settings(FAKE_ENGINE_COMFY_RUNNING="true", FAKE_ENGINE_EXEC_STATUS="3")
        with mock.patch.object(runtime, "warn") as warn, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runtime.smoke_gpu(settings, Engine("docker", settings), self.variant(settings), pinned), 3)
        warn.assert_called_once_with("the GPU smoke test failed with status 3")


if __name__ == "__main__":
    unittest.main()
