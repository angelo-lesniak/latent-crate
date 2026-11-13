"""Engine detection, the Compose invocation, the lock, the stack record,
and the port check, against the fake engine."""

from __future__ import annotations

import dataclasses
import fcntl
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import textwrap
import threading
import unittest
from unittest.mock import patch

from latentcrate import LatentCrateError, doctor, engine, env
from tests.support import Scratch

# A process that holds the project lock under `build` until its stdin closes.
HOLDER = textwrap.dedent(
    """
    import os, sys
    from pathlib import Path
    from latentcrate import engine, env
    settings = env.load(Path(sys.argv[1]), dict(os.environ))
    with engine.Engine("docker", settings).lock("build"):
        print("held", flush=True)
        sys.stdin.readline()
    """
)


def ipv6_loopback_available() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Scratch()
        self.addCleanup(self.scratch.close)

    def settings(self, **overrides: str) -> env.Settings:
        return env.load(self.scratch.root, self.scratch.environment(**overrides))

    def test_requested_engine_is_used_and_unknown_values_are_refused(self) -> None:
        self.assertEqual(engine.detect(self.settings(CONTAINER_ENGINE="docker")).name, "docker")
        self.assertEqual(engine.detect(self.settings(CONTAINER_ENGINE="podman")).name, "podman")
        with self.assertRaisesRegex(LatentCrateError, "unsupported CONTAINER_ENGINE=lxc"):
            engine.detect(self.settings(CONTAINER_ENGINE="lxc"))

    def test_podman_is_preferred_and_must_be_rootless_with_crun(self) -> None:
        self.assertEqual(engine.detect(self.settings(CONTAINER_ENGINE="")).name, "podman")
        self.assertEqual(engine.detect(self.settings(CONTAINER_ENGINE="", FAKE_PODMAN_ROOTLESS="false")).name, "docker")
        self.assertEqual(engine.detect(self.settings(CONTAINER_ENGINE="", FAKE_PODMAN_RUNTIME="runc")).name, "docker")
        with self.assertRaisesRegex(LatentCrateError, "requires a rootless engine; run: bash bin/latentcrate doctor"):
            engine.detect(self.settings(CONTAINER_ENGINE="podman", FAKE_PODMAN_ROOTLESS="false"))
        with self.assertRaises(LatentCrateError) as refused:
            engine.detect(self.settings(CONTAINER_ENGINE="podman", FAKE_PODMAN_RUNTIME="runc"))
        self.assertEqual(
            str(refused.exception),
            "rootless Podman with keep-groups requires crun (detected: runc); "
            'set runtime = "crun" under [engine] in ~/.config/containers/containers.conf; '
            "run: bash bin/latentcrate doctor",
        )

    def test_detect_refuses_an_unusable_engine_with_the_sentence_doctor_prints(self) -> None:
        settings = self.settings()
        podman = engine.inspect_engine("podman", settings.subprocess_env())
        for facts in (
            engine.EngineFacts(name="docker"),
            engine.EngineFacts(name="docker", present=True, path="/mnt/c/docker.exe", windows_client=True),
            engine.EngineFacts(name="docker", present=True, path="/usr/bin/docker", error="cannot connect"),
            engine.EngineFacts(name="podman", present=True, path="/usr/bin/podman", reachable=True),
            dataclasses.replace(podman, rootless="false"),
            dataclasses.replace(podman, runtime="runc"),
            dataclasses.replace(podman, runtime=""),
        ):
            with self.subTest(facts=facts):
                self.assertFalse(facts.usable)
                sentence = [item for item in doctor._engine_findings(facts, False) if item.level == "fail"]
                self.assertEqual(len(sentence), 1)
                with self.assertRaises(LatentCrateError) as refused:
                    engine.detect(settings, selection=engine.Selection(chosen=facts))
                self.assertEqual(str(refused.exception), f"{sentence[0].message}; {engine.RUN_DOCTOR}")
        self.assertTrue(podman.usable)
        self.assertEqual(engine.unusable_reason(podman), "")

    def test_docker_compose_plugin_under_podman_is_refused_only_when_launching(self) -> None:
        settings = self.settings(CONTAINER_ENGINE="podman", FAKE_PODMAN_COMPOSE_DOCKER_PLUGIN="true")
        with self.assertRaisesRegex(LatentCrateError, "drops CDI GPU devices; install podman-compose"):
            engine.detect(settings)
        self.assertEqual(engine.detect(settings, launching=False).name, "podman")

    def test_a_windows_client_under_wsl_is_unusable(self) -> None:
        settings = self.settings(CONTAINER_ENGINE="docker")
        exe = "/mnt/c/Program Files/Docker/docker.exe"
        with (
            patch.object(engine, "running_under_wsl", return_value=True),
            patch.object(shutil, "which", return_value=exe),
        ):
            facts = engine.inspect_engine("docker", settings.subprocess_env())
            self.assertEqual((facts.present, facts.path, facts.windows_client, facts.usable), (True, exe, True, False))
            with self.assertRaisesRegex(LatentCrateError, re.escape(f"not {exe}")):
                engine.detect(settings)
            with self.assertRaisesRegex(LatentCrateError, "neither Podman nor Docker"):
                engine.detect(self.settings(CONTAINER_ENGINE=""))
        # Outside WSL the name is only a name.
        with patch.object(engine, "running_under_wsl", return_value=False):
            self.assertFalse(engine.windows_client(exe))
        with patch.object(engine.Path, "read_text", return_value="6.6.87.2-microsoft-standard-WSL2\n"):
            self.assertTrue(engine.running_under_wsl())
        with patch.object(engine.Path, "read_text", side_effect=OSError):
            self.assertFalse(engine.running_under_wsl())

    def test_compose_file_list_follows_engine_gpu_and_frontend_mode(self) -> None:
        docker = engine.detect(self.settings(CONTAINER_ENGINE="docker"))
        trace = docker.compose("ps", capture=True).stdout
        self.assertIn(
            "args=compose --file compose.yaml --file compose.docker.yaml --file compose.gpu.yaml "
            "--env-file pins.env ps",
            trace,
        )
        self.assertNotIn("compose.podman.yaml", trace)
        podman = engine.detect(self.settings(CONTAINER_ENGINE="podman"))
        trace = podman.compose("ps", gpu=False, frontend_dist=True, capture=True).stdout
        self.assertIn(
            "args=compose --file compose.yaml --file compose.podman.yaml --file compose.frontend-dist.yaml "
            "--env-file pins.env ps",
            trace,
        )
        self.assertNotIn("compose.gpu.yaml", trace)

    def test_buildah_format_is_set_on_podman_only(self) -> None:
        for name, expected in (("docker", None), ("podman", "docker")):
            with patch.object(engine, "_run") as run:
                run.return_value.returncode = 0
                engine.Engine(name, self.settings()).compose("config")
            self.assertEqual(run.call_args.args[1].get("BUILDAH_FORMAT"), expected)
            self.assertNotIn("COMPOSE_REMOVE_ORPHANS", run.call_args.args[1])
            self.assertEqual(run.call_args.kwargs["cwd"], self.scratch.root)

    def test_token_and_pins_never_reach_the_engine_environment(self) -> None:
        settings = self.settings(CONTAINER_ENGINE="docker", HF_TOKEN="secret-fixture", COMFYUI_REF="from-shell")
        self.assertEqual(settings.hf_token, "secret-fixture")
        # The fake engine exits 1 when HF_TOKEN is in its environment.
        result = engine.detect(settings).compose("ps", capture=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn(" env_keys= args=", result.stdout)
        self.assertNotIn("COMFYUI_REF", engine.Engine("docker", settings).env)

    def test_the_lock_is_per_project_and_names_its_holder_while_it_waits(self) -> None:
        settings = self.settings()
        docker, podman = engine.Engine("docker", settings), engine.Engine("podman", settings)
        self.assertEqual(docker.lock_file, self.scratch.root / "build" / "locks" / "latentcrate.lock")
        self.assertEqual(docker.lock_file, podman.lock_file)
        for attempt in range(3):
            with self.subTest(attempt=attempt):
                holder = subprocess.Popen(
                    [sys.executable, "-c", HOLDER, str(self.scratch.root)],
                    cwd=self.scratch.root,
                    env=self.scratch.environment(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    text=True,
                )
                self.assertEqual(holder.stdout.readline(), "held\n")
                self.assertEqual(docker.lock_file.read_text(), f"{holder.pid} build\n")
                release = threading.Timer(0.3, holder.stdin.close)
                with patch.object(engine, "warn") as warn:
                    release.start()
                    with docker.lock("up"):
                        self.assertEqual(docker.lock_file.read_text(), f"{os.getpid()} up\n")
                self.assertEqual(holder.wait(5), 0)
                holder.stdout.close()
                warn.assert_called_once_with(f"waiting for `build` (pid {holder.pid}) to finish")
        # A caller that names no command is still serialized and reported.
        with docker.lock():
            self.assertEqual(docker.lock_file.read_text(), f"{os.getpid()}\n")

    def test_a_lock_file_with_binary_content_still_names_a_waiting_message(self) -> None:
        docker = engine.Engine("docker", self.settings())
        docker.lock_file.parent.mkdir(parents=True)
        docker.lock_file.write_bytes(b"\xff\xfe\x00 up")
        # A second open file description of the same file conflicts under flock.
        holder = os.open(docker.lock_file, os.O_RDWR)
        fcntl.flock(holder, fcntl.LOCK_EX)
        release = threading.Timer(0.3, os.close, [holder])
        with patch.object(engine, "warn") as warn:
            release.start()
            with docker.lock("build"):
                self.assertEqual(docker.lock_file.read_text(), f"{os.getpid()} build\n")
        warn.assert_called_once()
        self.assertRegex(warn.call_args.args[0], r"^waiting for `up` \(pid .*\) to finish$")

    def test_the_stack_record_decides_the_engine_and_refuses_a_disagreeing_setting(self) -> None:
        # Podman is usable, so detection would pick it; a stack recorded on
        # Docker keeps `down` and `status` on Docker until it is forgotten.
        engine.Engine("docker", self.settings()).record_stack()
        self.assertEqual(
            json.loads((self.scratch.root / "build" / "stack.json").read_text()),
            {"engine": "docker", "project": "latentcrate"},
        )
        self.assertEqual(engine.recorded_engine(self.settings()), "docker")
        self.assertEqual(engine.recorded_engine(self.settings(COMPOSE_PROJECT_NAME="other")), "")
        self.assertEqual(engine.detect(self.settings(CONTAINER_ENGINE=""), launching=False).name, "docker")
        self.assertEqual(engine.detect(self.settings(CONTAINER_ENGINE="docker")).name, "docker")
        refusal = (
            "the stack runs on docker, not podman (build/stack.json); "
            "run: CONTAINER_ENGINE=docker bash bin/latentcrate down"
        )
        with self.assertRaisesRegex(LatentCrateError, re.escape(refusal)):
            engine.requested_engine(self.settings(CONTAINER_ENGINE="podman"))
        with self.assertRaisesRegex(LatentCrateError, re.escape(refusal)):
            engine.detect(self.settings(CONTAINER_ENGINE="podman"), launching=False)
        # `up` passes what doctor gathered; a selection that disagrees with
        # the record is refused the same way, and a consistent one is used
        # without another probe.
        probed = engine.select(self.settings(CONTAINER_ENGINE="docker").subprocess_env(), "docker")
        with patch.object(engine, "select") as select:
            self.assertEqual(engine.detect(self.settings(), selection=probed).name, "docker")
        select.assert_not_called()
        other = engine.select(self.settings().subprocess_env(), "podman")
        with self.assertRaisesRegex(LatentCrateError, "the stack runs on docker, not podman"):
            engine.detect(self.settings(), selection=other)
        # Another project's record, and no record, leave detection alone.
        engine.Engine("docker", self.settings(COMPOSE_PROJECT_NAME="other")).record_stack()
        self.assertEqual(engine.detect(self.settings(CONTAINER_ENGINE="")).name, "podman")
        engine.Engine("docker", self.settings()).forget_stack()
        self.assertEqual(engine.detect(self.settings(CONTAINER_ENGINE="")).name, "podman")
        engine.Engine("docker", self.settings()).forget_stack()
        record = self.scratch.root / "build" / "stack.json"
        record.write_text("{")
        with self.assertRaisesRegex(LatentCrateError, "cannot read the stack record .*stack.json: .*; remove it"):
            engine.detect(self.settings(CONTAINER_ENGINE=""))
        record.write_text(json.dumps({"engine": "lxc", "project": "latentcrate"}))
        refusal = f"the stack record {record} names an unknown engine lxc; remove it if no stack is running"
        for settings in (self.settings(CONTAINER_ENGINE=""), self.settings(CONTAINER_ENGINE="docker")):
            with self.assertRaisesRegex(LatentCrateError, re.escape(refusal)):
                engine.detect(settings)
        self.assertEqual(engine.recorded_engine(self.settings(COMPOSE_PROJECT_NAME="other")), "")

    def test_a_free_port_passes_and_a_listener_refuses_with_its_cause(self) -> None:
        docker = engine.Engine("docker", self.settings())
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = str(listener.getsockname()[1])
            self.assertIn("in use", engine.bind_error("127.0.0.1", port))
            with self.assertRaisesRegex(LatentCrateError, f"cannot use host port {port} on 127.0.0.1: .*in use"):
                docker.require_port(port)
        self.assertEqual(engine.bind_error("127.0.0.1", port), "")
        docker.require_port(port)

    def test_the_port_of_this_projects_service_passes_whatever_host_address_the_engine_reports(self) -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = str(listener.getsockname()[1])
            running = engine.detect(
                self.settings(
                    CONTAINER_ENGINE="docker",
                    FAKE_ENGINE_COMFY_RUNNING="true",
                    FAKE_ENGINE_COMFY_PORT=port,
                    FAKE_ENGINE_COMFY_HOST="::1",
                )
            )
            self.assertTrue(running.own_service_publishes(port))
            running.require_port(port)
            foreign = dict(running.containers()[0])
            foreign["Config"] = {"Labels": {engine.SERVICE_LABEL: "other"}}
            refusal = f"cannot use host port {port} on 127.0.0.1: .*in use"
            with patch.object(engine.Engine, "containers", return_value=[foreign]):
                self.assertFalse(running.own_service_publishes(port))
                with self.assertRaisesRegex(LatentCrateError, refusal):
                    running.require_port(port)
            # A container without labels, and one of another Compose project, are not this service.
            for override in ({"FAKE_ENGINE_NO_LABELS": "true"}, {"FAKE_ENGINE_PROJECT": "other"}):
                with self.subTest(override=override):
                    other = engine.Engine(
                        "docker",
                        self.settings(FAKE_ENGINE_COMFY_RUNNING="true", FAKE_ENGINE_COMFY_PORT=port, **override),
                    )
                    self.assertFalse(other.own_service_publishes(port))
                    with self.assertRaisesRegex(LatentCrateError, refusal):
                        other.require_port(port)
            self.assertEqual(
                engine.Engine(
                    "docker", self.settings(FAKE_ENGINE_COMFY_RUNNING="true", FAKE_ENGINE_PROJECT="other")
                ).containers(),
                [],
            )

    def test_the_bind_probe_uses_the_configured_address_family(self) -> None:
        if not ipv6_loopback_available():
            self.skipTest("IPv6 loopback is not available on this host")
        with socket.socket(socket.AF_INET6) as listener:
            listener.bind(("::1", 0))
            listener.listen(1)
            port = str(listener.getsockname()[1])
            self.assertIn("in use", engine.bind_error("[::1]", port))
            docker = engine.Engine("docker", self.settings(COMFY_BIND_ADDRESS="[::1]"))
            with self.assertRaisesRegex(LatentCrateError, re.escape(f"cannot use host port {port} on [::1]: ")):
                docker.require_port(port)
        self.assertEqual(engine.bind_error("[::1]", port), "")

    def test_inspect_engine_reads_storage_root_server_version_and_cdi_directories(self) -> None:
        env_vars = self.settings(FAKE_ENGINE_STORAGE_ROOT=str(self.scratch.tmp)).subprocess_env()
        facts = engine.inspect_engine("docker", env_vars)
        self.assertEqual((facts.usable, facts.server_version), (True, "29.7.2-fake"))
        self.assertEqual((facts.storage_root, facts.cdi_spec_dirs), (str(self.scratch.tmp), ()))
        podman = engine.inspect_engine("podman", env_vars)
        self.assertEqual((podman.server_version, podman.storage_root), ("", str(self.scratch.tmp)))
        env_vars = self.settings(
            FAKE_ENGINE_STORAGE_ROOT=str(self.scratch.tmp / "missing"), FAKE_DOCKER_CDI_DIRS="/etc/cdi:/var/run/cdi"
        ).subprocess_env()
        facts = engine.inspect_engine("docker", env_vars)
        self.assertEqual((facts.storage_root, facts.cdi_spec_dirs), ("/", ("/etc/cdi", "/var/run/cdi")))
        self.assertEqual(engine.inspect_engine("podman", env_vars).cdi_spec_dirs, ())

    def test_container_lookup_removal_and_state(self) -> None:
        idle = engine.detect(self.settings(CONTAINER_ENGINE="docker"))
        self.assertEqual(idle.container_ids(engine.SERVICE), [])
        self.assertEqual(idle.containers(), [])
        running = engine.detect(self.settings(CONTAINER_ENGINE="docker", FAKE_ENGINE_COMFY_RUNNING="true"))
        self.assertEqual(running.container_ids(engine.SERVICE), ["fake-comfy-container"])
        owned = running.containers()
        self.assertEqual([item["Id"] for item in owned], ["fake-comfy-container"])
        self.assertEqual(owned[0]["Config"]["Labels"][engine.PROJECT_LABEL], "latentcrate")
        self.assertTrue(engine.publishes(owned[0], self.scratch.port))
        self.assertFalse(engine.publishes(owned[0], "1"))
        self.assertEqual(running.container_state("fake-comfy-container"), "running")
        running.remove_containers(owned)
        exited = engine.detect(self.settings(CONTAINER_ENGINE="docker", FAKE_ENGINE_COMFY_EXITED="true"))
        self.assertEqual(exited.container_ids(engine.SERVICE), [])
        self.assertEqual(exited.container_ids(engine.SERVICE, all_states=True), ["fake-comfy-container"])
        self.assertEqual(
            engine.detect(self.settings(CONTAINER_ENGINE="docker", FAKE_ENGINE_STATE="exited")).container_state("x"),
            "exited",
        )

    def test_removal_tolerates_a_vanished_container_but_reports_engine_errors(self) -> None:
        docker = engine.Engine("docker", self.settings())
        success = subprocess.CompletedProcess([], 0, "", "")
        missing = subprocess.CompletedProcess([], 1, "", "No such container: comfy")
        with patch.object(engine, "_run", side_effect=[success, missing]):
            docker.remove_containers([{"Id": "comfy"}])
        removing = subprocess.CompletedProcess(
            [], 1, "", "Error response from daemon: removal of container comfy is already in progress"
        )
        with (
            patch.object(engine, "_run", side_effect=[success, removing, missing]) as run,
            patch.object(engine.time, "sleep") as sleep,
        ):
            docker.remove_containers([{"Id": "comfy"}])
        self.assertEqual([call.args[0][1] for call in run.call_args_list], ["stop", "rm", "rm"])
        sleep.assert_called_once_with(0.1)
        denied = subprocess.CompletedProcess([], 1, "", "permission denied")
        with patch.object(engine, "_run", return_value=denied):
            with self.assertRaisesRegex(LatentCrateError, "stop failed: permission denied"):
                docker.remove_containers([{"Id": "comfy"}])

    def test_a_failing_engine_command_becomes_a_user_facing_error(self) -> None:
        docker = engine.detect(self.settings(CONTAINER_ENGINE="docker"))
        # The fake engine exits 1 for an invocation the wrapper never makes.
        with self.assertRaisesRegex(LatentCrateError, "docker bogus failed: fake engine: unsupported"):
            docker.command("bogus", capture=True)
        self.assertEqual(docker.command("bogus", capture=True, check=False).returncode, 1)
        failure = subprocess.CompletedProcess([], 1, "", "invalid integer in services.comfy.pids_limit")
        with patch.object(engine, "_run", return_value=failure):
            with self.assertRaisesRegex(LatentCrateError, "compose config failed .*services.comfy.pids_limit"):
                docker.compose("config", capture=True)

    def test_run_tool_names_a_missing_tool_and_the_first_line_of_a_failure(self) -> None:
        env_vars = self.settings().subprocess_env()
        self.assertEqual(engine.run_tool(["no-such-tool"], env_vars), (None, "no-such-tool was not found"))
        self.assertEqual(
            engine.run_tool(["docker", "bogus"], env_vars)[1], "fake engine: unsupported invocation: bogus"
        )
        self.assertEqual(engine.run_tool(["docker", "--version"], env_vars), ("Docker version fake\n", ""))
        self.assertIsNone(engine.tool_output(["docker", "bogus"], env_vars))
        # The fake engine answers the exact templates the wrapper sends and nothing else.
        self.assertEqual(
            engine.run_tool(["docker", "info", "--format", "{{.Bogus}}"], env_vars)[1],
            "fake engine: unknown format {{.Bogus}}",
        )


if __name__ == "__main__":
    unittest.main()
