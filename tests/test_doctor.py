"""The host doctor: the judgement on hand-built facts, the report of run(),
and gather() against the fake engine on a scratch checkout."""

from __future__ import annotations

import contextlib
import dataclasses
import io
import os
import socket
import unittest
from pathlib import Path
from unittest import mock

from latentcrate import doctor, engine, env
from latentcrate.doctor import Finding, GpuFacts, HostFacts, StorageFact
from latentcrate.engine import EngineFacts, Selection
from latentcrate.env import HostPath
from tests.support import Scratch

MIN_DRIVER_MAJOR = 580
NOTE = (
    "       CDI checks read configuration only; "
    "the first up and bash bin/latentcrate smoke-gpu prove device resolution."
)
RTX_5090 = GpuFacts("0", "GPU-abc", "12.0", "615.78.08", "NVIDIA GeForce RTX 5090")
RTX_3090 = GpuFacts("1", "GPU-def", "8.6", "615.78.08", "NVIDIA GeForce RTX 3090")
DATA = HostPath("the data directory", Path("/srv/data"), "COMFY_DATA_DIR", mounted=True, writable=True)
LOCAL_NODES = HostPath(
    "the local custom-node directory", Path("/srv/local-nodes"), "COMFY_LOCAL_NODES_DIR", mounted=True
)
SMI_LINE = "0, GPU-abc, 12.0, 615.78.08, NVIDIA GeForce RTX 5090"
IN_USE = "[Errno 98] Address already in use"


def podman_facts(**changes: object) -> EngineFacts:
    facts = EngineFacts(
        name="podman",
        present=True,
        path="/usr/bin/podman",
        version_line="podman version 6.1.0-fake",
        reachable=True,
        provider="podman-compose version 1.6.0-fake",
        rootless="true",
        runtime="crun",
        keeps_cdi=True,
        storage_root="/var/lib/store",
    )
    return dataclasses.replace(facts, **changes)


def docker_facts(**changes: object) -> EngineFacts:
    facts = EngineFacts(
        name="docker",
        present=True,
        path="/usr/bin/docker",
        version_line="Docker version fake",
        reachable=True,
        provider="Docker Compose version fake",
        server_version="29.7.2-fake",
        storage_root="/var/lib/docker",
        cdi_spec_dirs=("/etc/cdi", "/var/run/cdi"),
    )
    return dataclasses.replace(facts, **changes)


def healthy_facts(**changes: object) -> HostFacts:
    """Rootless Podman with crun and podman-compose, an RTX 5090 on a new
    driver, the CDI spec in place, loopback, a free port."""
    facts = HostFacts(
        sysname="Linux",
        machine="x86_64",
        engine=podman_facts(),
        nvidia_smi_present=True,
        gpus=[RTX_5090],
        cuda_arch_list="12.0",
        cdi_spec_present=True,
        storage_free_kb=100 * 1024 * 1024,
        storage=[StorageFact(DATA, True, True, True), StorageFact(LOCAL_NODES, True, True, True)],
        models_gid=1000,
        host_model_gid="1000",
        bind_address="127.0.0.1",
        port="4207",
    )
    return dataclasses.replace(facts, **changes)


def evaluate(facts: HostFacts, *, allow_no_gpu: bool = False, sage: str = "off", minimum: int = MIN_DRIVER_MAJOR):
    """The findings rendered the way run() prints them."""
    findings = doctor.evaluate(facts, min_driver_major=minimum, allow_no_gpu=allow_no_gpu, sage=sage)
    return [doctor.format_finding(finding) for finding in findings]


def failures(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("[fail] ")]


def warnings(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("[warn] ")]


class EvaluateTests(unittest.TestCase):
    def setUp(self) -> None:
        # The judgement reads facts only: no engine, no nvidia-smi, no socket.
        for target in ("subprocess.run", "socket.socket", "shutil.which"):
            patcher = mock.patch(target, side_effect=AssertionError(f"{target} called during evaluate"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_healthy_podman_host_passes_every_check_in_report_order(self) -> None:
        lines = evaluate(healthy_facts())
        self.assertEqual(failures(lines), [])
        self.assertEqual(warnings(lines), [])
        self.assertEqual(
            lines,
            [
                "[ok] checking the smaller image without SageAttention",
                "[ok] Linux on x86-64",
                "[ok] podman version 6.1.0-fake",
                "[ok] podman engine is reachable",
                "[ok] podman-compose version 1.6.0-fake",
                "[ok] Podman engine is rootless",
                "[ok] Podman uses crun, which supports keep-groups",
                "[ok] the Podman compose provider keeps CDI device requests",
                "[ok] engine storage at /var/lib/store has 100 GB free",
                "[ok] NVIDIA GPU: NVIDIA GeForce RTX 5090, driver 615.78.08, compute capability 12.0",
                "[ok] NVIDIA driver 615.78.08 satisfies the pinned minimum major 580",
                "[ok] COMFY_CUDA_ARCH_LIST covers compute capability 12.0",
                "[ok] a CDI spec for nvidia.com/gpu exists under /etc/cdi or /var/run/cdi",
                NOTE,
                "[ok] the data directory exists: /srv/data",
                "[ok] the local custom-node directory exists: /srv/local-nodes",
            ],
        )

    def test_the_sage_line_names_the_image_the_mode_selects(self) -> None:
        self.assertEqual(
            evaluate(healthy_facts(), sage="available")[0], "[ok] checking the SageAttention-capable image"
        )
        self.assertEqual(evaluate(healthy_facts(), sage="global")[0], "[ok] checking the SageAttention-capable image")

    def test_a_non_linux_host_fails_before_the_architecture_check(self) -> None:
        lines = evaluate(healthy_facts(sysname="Darwin", machine="arm64"))
        self.assertEqual(
            failures(lines),
            ["[fail] run LatentCrate in a Linux environment (native Linux or WSL2), not directly from Windows"],
        )
        self.assertNotIn("[fail] unsupported host architecture: arm64; the pinned CUDA image is x86-64 only", lines)

    def test_a_non_x86_64_host_fails(self) -> None:
        lines = evaluate(healthy_facts(machine="aarch64"))
        self.assertEqual(
            failures(lines), ["[fail] unsupported host architecture: aarch64; the pinned CUDA image is x86-64 only"]
        )

    def test_wsl_warns_once_and_annotates_the_port_finding(self) -> None:
        lines = evaluate(healthy_facts(wsl=True, port_error=IN_USE))
        self.assertEqual(
            warnings(lines),
            [
                "[warn] WSL detected; WSL2 is not validated, and docs/validation.md#known-gaps "
                "lists what a run must show"
            ],
        )
        self.assertEqual(
            failures(lines),
            [
                f"[fail] cannot use host port 4207 on 127.0.0.1: {IN_USE}; under WSL2 a Windows-side program "
                "can hold a port invisibly to Linux tools (check with netstat -ano from Windows)"
            ],
        )

    def test_a_disagreeing_container_engine_fails_with_the_stack_wording(self) -> None:
        refusal = engine.stack_runs_on("docker", "podman")
        lines = evaluate(healthy_facts(engine_refusal=refusal))
        self.assertEqual(failures(lines), [f"[fail] {refusal}"])
        self.assertIn("[ok] podman engine is reachable", lines)

    def test_unsupported_container_engine_fails(self) -> None:
        lines = evaluate(healthy_facts(engine=None, unsupported_engine="lxc", cdi_spec_present=False))
        self.assertEqual(failures(lines), ["[fail] unsupported CONTAINER_ENGINE=lxc"])
        self.assertFalse(any("CDI" in line for line in lines))

    def test_no_usable_engine_reports_every_present_candidate(self) -> None:
        candidates = [podman_facts(reachable=False, error="cannot connect"), docker_facts(provider="")]
        lines = evaluate(healthy_facts(engine=None, candidates=candidates))
        self.assertEqual(
            failures(lines),
            [
                "[fail] neither Podman nor Docker has both a reachable engine and Compose provider",
                "[fail] podman engine is not reachable: cannot connect",
                "[fail] docker has no working Compose provider",
            ],
        )
        self.assertFalse(any("engine storage" in line for line in lines))

    def test_the_engine_block_stops_at_the_first_missing_piece(self) -> None:
        lines = evaluate(healthy_facts(engine=EngineFacts(name="docker")))
        self.assertEqual(failures(lines), ["[fail] docker was not found"])
        windows = EngineFacts(name="docker", present=True, path="/mnt/c/docker.exe", windows_client=True)
        lines = evaluate(healthy_facts(engine=windows))
        self.assertEqual(failures(lines), [f"[fail] {engine.windows_client_refusal('docker', '/mnt/c/docker.exe')}"])

    def test_rootful_podman_and_a_runtime_other_than_crun_fail(self) -> None:
        lines = evaluate(healthy_facts(engine=podman_facts(rootless="false", runtime="runc")))
        self.assertEqual(
            failures(lines),
            [
                "[fail] the supported Podman path requires a rootless engine",
                "[fail] rootless Podman with keep-groups requires crun (detected: runc); "
                'set runtime = "crun" under [engine] in ~/.config/containers/containers.conf',
            ],
        )
        lines = evaluate(healthy_facts(engine=podman_facts(runtime="")))
        self.assertIn(
            "[fail] rootless Podman with keep-groups requires crun (detected: unknown); "
            'set runtime = "crun" under [engine] in ~/.config/containers/containers.conf',
            lines,
        )

    def test_the_docker_compose_plugin_under_podman_is_a_gpu_problem(self) -> None:
        facts = healthy_facts(engine=podman_facts(provider="Docker Compose version v2.0.0-fake", keeps_cdi=False))
        message = (
            "the Podman compose provider is the Docker Compose plugin, which drops CDI GPU devices; "
            "install podman-compose"
        )
        self.assertEqual(failures(evaluate(facts)), [f"[fail] {message}"])
        lines = evaluate(facts, allow_no_gpu=True)
        self.assertEqual(failures(lines), [])
        self.assertEqual(warnings(lines), [f"[warn] {message} (--allow-no-gpu was supplied)"])

    def test_docker_skips_the_podman_only_checks(self) -> None:
        lines = evaluate(healthy_facts(engine=docker_facts()))
        self.assertEqual(failures(lines), [])
        self.assertIn("[ok] docker engine is reachable", lines)
        self.assertIn("[ok] a CDI spec for nvidia.com/gpu exists in: /etc/cdi /var/run/cdi", lines)
        self.assertFalse(any("Podman" in line for line in lines))

    def test_engine_storage_below_the_build_size_warns(self) -> None:
        lines = evaluate(healthy_facts(storage_free_kb=10 * 1024 * 1024))
        self.assertEqual(
            warnings(lines),
            ["[warn] engine storage at /var/lib/store has only 10 GB free; a full LatentCrate build needs about 75 GB"],
        )
        lines = evaluate(healthy_facts(storage_free_kb=None, engine=podman_facts(storage_root="")))
        self.assertEqual(
            warnings(lines),
            [
                "[warn] could not measure free disk space for engine storage at /; "
                "ensure about 75 GB are free before building"
            ],
        )

    def test_missing_nvidia_smi_fails_unless_no_gpu_is_allowed(self) -> None:
        facts = healthy_facts(nvidia_smi_present=False, gpus=[], gpu_error="nvidia-smi was not found")
        self.assertEqual(
            failures(evaluate(facts)), ["[fail] nvidia-smi was not found; the supported GPU path is unavailable"]
        )
        lines = evaluate(facts, allow_no_gpu=True)
        self.assertEqual(failures(lines), [])
        self.assertIn(
            "[warn] nvidia-smi was not found; the supported GPU path is unavailable (--allow-no-gpu was supplied)",
            lines,
        )

    def test_a_failing_nvidia_smi_reports_its_first_line(self) -> None:
        facts = healthy_facts(gpus=[], gpu_error="NVIDIA-SMI has failed")
        self.assertEqual(
            failures(evaluate(facts)),
            ["[fail] nvidia-smi is installed but could not query a GPU: NVIDIA-SMI has failed"],
        )
        facts = healthy_facts(gpus=[], gpu_error="")
        self.assertEqual(failures(evaluate(facts)), ["[fail] nvidia-smi is installed but could not query a GPU"])

    def test_an_old_or_unparseable_driver_fails_against_the_pinned_minimum(self) -> None:
        old = dataclasses.replace(RTX_5090, driver_version="570.12")
        lines = evaluate(healthy_facts(gpus=[old]))
        self.assertEqual(
            failures(lines),
            ["[fail] NVIDIA driver 570.12 is older than the pinned minimum major 580 (CUDA_MIN_DRIVER_MAJOR)"],
        )
        self.assertIn(
            "[ok] NVIDIA driver 570.12 satisfies the pinned minimum major 570",
            evaluate(healthy_facts(gpus=[old]), minimum=570),
        )
        odd = dataclasses.replace(RTX_5090, driver_version="unknown")
        self.assertEqual(len(failures(evaluate(healthy_facts(gpus=[odd])))), 1)

    def test_an_uncovered_compute_capability_names_the_list(self) -> None:
        lines = evaluate(healthy_facts(gpus=[RTX_3090]))
        self.assertEqual(
            failures(lines),
            ["[fail] COMFY_CUDA_ARCH_LIST=12.0 does not cover compute capability 8.6; add it to the list in .env"],
        )
        lines = evaluate(healthy_facts(gpus=[RTX_3090]), allow_no_gpu=True)
        self.assertEqual(failures(lines), [])
        self.assertEqual(len(warnings(lines)), 1)

    def test_the_arch_list_accepts_semicolons_and_ptx_suffixes(self) -> None:
        for arch_list in ("12.0;8.6+PTX", "8.6;12.0"):
            lines = evaluate(healthy_facts(gpus=[RTX_5090, RTX_3090], cuda_arch_list=arch_list))
            self.assertEqual(failures(lines), [], arch_list)
            self.assertIn("[ok] COMFY_CUDA_ARCH_LIST covers compute capability 8.6", lines)
        self.assertFalse(doctor.arch_list_covers("12.0", "12"))
        self.assertFalse(doctor.arch_list_covers("", "12.0"))

    def test_a_refused_arch_list_is_one_failure_in_place_of_the_coverage_lines(self) -> None:
        refusal = "COMFY_CUDA_ARCH_LIST='abc' is not a ;-separated list of CUDA architectures such as 12.0 or 8.6+PTX"
        lines = evaluate(healthy_facts(cuda_arch_list="", cuda_arch_list_refusal=refusal))
        self.assertEqual(failures(lines), [f"[fail] {refusal}"])
        self.assertFalse(any("covers compute capability" in line for line in lines))

    def test_gpu_device_selects_the_gpu_by_index_or_uuid(self) -> None:
        both = healthy_facts(gpus=[RTX_5090, RTX_3090])
        for device in ("nvidia.com/gpu=0", "nvidia.com/gpu=GPU-abc"):
            lines = evaluate(dataclasses.replace(both, gpu_device=device))
            self.assertEqual(failures(lines), [], device)
            self.assertEqual(warnings(lines), [], device)
            self.assertNotIn("[ok] COMFY_CUDA_ARCH_LIST covers compute capability 8.6", lines)
        lines = evaluate(dataclasses.replace(both, gpu_device="nvidia.com/gpu=1"))
        self.assertEqual(len(failures(lines)), 1)
        self.assertIn("compute capability 8.6", failures(lines)[0])

    def test_an_unmapped_gpu_device_warns_and_checks_every_gpu(self) -> None:
        lines = evaluate(healthy_facts(gpus=[RTX_5090, RTX_3090], gpu_device="nvidia.com/gpu=7"))
        self.assertEqual(
            warnings(lines),
            ["[warn] could not map GPU_DEVICE=nvidia.com/gpu=7 to an nvidia-smi index or GPU UUID; checking every GPU"],
        )
        self.assertIn("[ok] COMFY_CUDA_ARCH_LIST covers compute capability 12.0", lines)
        self.assertEqual(len(failures(lines)), 1)

    def test_a_missing_podman_cdi_spec_names_the_generate_command(self) -> None:
        lines = evaluate(healthy_facts(cdi_spec_present=False))
        self.assertEqual(
            failures(lines),
            [
                "[fail] no nvidia.com/gpu CDI spec was found under /etc/cdi or /var/run/cdi; "
                "run: sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml"
            ],
        )
        self.assertEqual(lines[lines.index(failures(lines)[0]) + 1], NOTE)

    def test_docker_without_cdi_directories_or_without_a_spec_is_a_gpu_problem(self) -> None:
        lines = evaluate(healthy_facts(engine=docker_facts(cdi_spec_dirs=()), cdi_spec_present=False))
        self.assertEqual(
            failures(lines),
            [
                '[fail] Docker reports no CDI spec directories; enable CDI in /etc/docker/daemon.json ("features": '
                '{"cdi": true}) or upgrade Docker so nvidia.com/gpu=all can resolve'
            ],
        )
        lines = evaluate(healthy_facts(engine=docker_facts(), cdi_spec_present=False), allow_no_gpu=True)
        self.assertEqual(failures(lines), [])
        self.assertIn(
            "[warn] no nvidia.com/gpu CDI spec was found in: /etc/cdi /var/run/cdi; "
            "run: sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml (--allow-no-gpu was supplied)",
            lines,
        )

    def test_the_cdi_checks_are_skipped_without_a_reachable_engine(self) -> None:
        lines = evaluate(healthy_facts(engine=podman_facts(reachable=False)))
        self.assertNotIn(NOTE, lines)
        self.assertFalse(any("CDI" in line for line in lines))

    def test_storage_entries_are_reported_per_label(self) -> None:
        models = HostPath("the models directory", Path("/srv/models"), "COMFY_MODELS_DIR", mounted=True, writable=True)
        managed = HostPath("the managed custom-node directory", Path("/srv/data/custom_nodes"), created_by_init=True)
        cache = HostPath("the cache directory", Path("/srv/cache"), "COMFY_CACHE_DIR", mounted=True, writable=True)
        storage = [
            StorageFact(DATA, False, False, False),
            StorageFact(models, True, True, False),
            StorageFact(LOCAL_NODES, True, True, False),
            StorageFact(managed, True, True, True),
            StorageFact(cache, True, False, True),
        ]
        lines = evaluate(healthy_facts(storage=storage))
        self.assertEqual(
            warnings(lines),
            ["[warn] the data directory does not exist yet: /srv/data (bin/latentcrate init creates it)"],
        )
        self.assertEqual(
            failures(lines),
            [
                "[fail] the models directory is not writable: /srv/models",
                "[fail] the local custom-node directory is not readable: /srv/local-nodes",
                "[fail] the cache directory is not a directory: /srv/cache",
            ],
        )
        self.assertIn("[ok] the managed custom-node directory exists: /srv/data/custom_nodes", lines)

    def test_a_models_group_other_than_host_model_gid_warns_under_docker_only(self) -> None:
        facts = healthy_facts(engine=docker_facts(), models_gid=1001)
        self.assertEqual(
            warnings(evaluate(facts)),
            ["[warn] the models directory's group is 1001 but HOST_MODEL_GID=1000; Docker may lack group access"],
        )
        self.assertEqual(warnings(evaluate(healthy_facts(models_gid=1001))), [])
        self.assertEqual(warnings(evaluate(healthy_facts(engine=docker_facts(), models_gid=None))), [])
        refused = healthy_facts(engine=docker_facts(), host_model_gid="", host_model_gid_refusal="HOST_MODEL_GID bad")
        self.assertEqual(failures(evaluate(refused)), ["[fail] HOST_MODEL_GID bad"])

    def test_a_refused_bind_address_fails_with_the_settings_wording(self) -> None:
        lines = evaluate(healthy_facts(bind_address="", bind_refusal=env.BIND_NOT_ACCEPTED))
        self.assertEqual(failures(lines), [f"[fail] {env.BIND_NOT_ACCEPTED}"])
        lines = evaluate(healthy_facts(bind_address="", bind_refusal=env.not_loopback("0.0.0.0")))
        self.assertEqual(failures(lines), [f"[fail] {env.not_loopback('0.0.0.0')}"])

    def test_an_acknowledged_remote_bind_warns(self) -> None:
        lines = evaluate(healthy_facts(bind_address="0.0.0.0"))
        self.assertEqual(warnings(lines), ["[warn] ComfyUI will bind beyond localhost: 0.0.0.0"])
        self.assertEqual(warnings(evaluate(healthy_facts(bind_address="[::1]"))), [])

    def test_port_findings(self) -> None:
        lines = evaluate(healthy_facts(port="", port_refusal="COMFY_PORT requires a port from 1 to 65535"))
        self.assertEqual(failures(lines), ["[fail] COMFY_PORT requires a port from 1 to 65535"])
        lines = evaluate(healthy_facts(port_error=IN_USE))
        self.assertEqual(failures(lines), [f"[fail] cannot use host port 4207 on 127.0.0.1: {IN_USE}"])
        lines = evaluate(healthy_facts(port_error=IN_USE, own_container_holds_port=True))
        self.assertEqual(failures(lines), [])
        unroutable = "[Errno 99] Cannot assign requested address"
        lines = evaluate(healthy_facts(bind_address="192.0.2.1", port_error=unroutable))
        self.assertEqual(failures(lines), [f"[fail] cannot use host port 4207 on 192.0.2.1: {unroutable}"])

    def test_allow_no_gpu_turns_every_gpu_problem_into_a_warning(self) -> None:
        facts = healthy_facts(
            engine=podman_facts(keeps_cdi=False),
            gpus=[dataclasses.replace(RTX_3090, driver_version="570.12")],
            cdi_spec_present=False,
        )
        self.assertEqual(len(failures(evaluate(facts))), 4)
        lines = evaluate(facts, allow_no_gpu=True)
        self.assertEqual(failures(lines), [])
        self.assertEqual(len(warnings(lines)), 4)
        self.assertTrue(all(line.endswith(" (--allow-no-gpu was supplied)") for line in warnings(lines)))

    def test_the_selection_is_what_detect_takes(self) -> None:
        self.assertEqual(healthy_facts().selection, Selection(chosen=podman_facts()))
        self.assertEqual(
            healthy_facts(engine=None, candidates=[docker_facts()], unsupported_engine="lxc").selection,
            Selection(candidates=(docker_facts(),), unsupported="lxc"),
        )


class RunTests(unittest.TestCase):
    def run_doctor(self, gathered: HostFacts, **options: object) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(doctor, "gather", return_value=gathered) as gather:
            status = doctor.run(
                mock.Mock(), min_driver_major=MIN_DRIVER_MAJOR, allow_no_gpu=False, sage="off", **options
            )
        if "facts" in options:
            gather.assert_not_called()
        return status, output.getvalue()

    def test_healthy_report_exits_zero_with_the_summary_line(self) -> None:
        status, output = self.run_doctor(healthy_facts())
        self.assertEqual(status, 0)
        lines = output.splitlines()
        self.assertEqual(lines[0], "== LatentCrate host doctor ==")
        self.assertEqual(lines[1], "[ok] checking the smaller image without SageAttention")
        self.assertEqual(lines[-2:], ["", "Doctor completed with 0 failure(s) and 0 warning(s)."])

    def test_failures_set_the_exit_status_and_notes_are_not_counted(self) -> None:
        status, output = self.run_doctor(healthy_facts(machine="aarch64", storage_free_kb=0, cdi_spec_present=False))
        self.assertEqual(status, 1)
        self.assertIn(NOTE, output.splitlines())
        self.assertTrue(output.endswith("Doctor completed with 2 failure(s) and 1 warning(s).\n"))

    def test_gathered_facts_are_taken_as_given(self) -> None:
        status, output = self.run_doctor(healthy_facts(), facts=healthy_facts(port_error=IN_USE))
        self.assertEqual(status, 1)
        self.assertIn(f"[fail] cannot use host port 4207 on 127.0.0.1: {IN_USE}", output)


class GatherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Scratch()
        self.addCleanup(self.scratch.close)

    def settings(self, **overrides: str) -> env.Settings:
        return env.load(self.scratch.root, self.scratch.environment(**overrides))

    def test_gather_reads_the_platform_the_engine_the_gpu_and_the_settings(self) -> None:
        cdi = self.scratch.tmp / "cdi"
        cdi.mkdir()
        (cdi / "nvidia.yaml").write_text("devices:\n- name: all\nkind: nvidia.com/gpu\n", encoding="utf-8")
        facts = doctor.gather(
            self.settings(
                FAKE_NVIDIA_SMI=SMI_LINE,
                FAKE_ENGINE_STORAGE_ROOT=str(self.scratch.tmp),
                FAKE_DOCKER_CDI_DIRS=str(cdi),
                GPU_DEVICE="nvidia.com/gpu=0",
                COMFY_CUDA_ARCH_LIST="12.0;8.6",
            )
        )
        self.assertEqual((facts.sysname, facts.machine), (os.uname().sysname, os.uname().machine))
        self.assertFalse(facts.wsl)
        self.assertEqual(facts.engine_refusal, "")
        assert facts.engine is not None
        self.assertEqual(
            (facts.engine.name, facts.engine.reachable, facts.engine.storage_root),
            ("docker", True, str(self.scratch.tmp)),
        )
        self.assertEqual(facts.engine.cdi_spec_dirs, (str(cdi),))
        self.assertEqual(facts.selection, Selection(chosen=facts.engine))
        self.assertTrue(facts.nvidia_smi_present)
        self.assertEqual(facts.gpus, [RTX_5090])
        self.assertEqual(facts.gpu_error, "")
        self.assertEqual((facts.gpu_device, facts.cuda_arch_list), ("nvidia.com/gpu=0", "12.0;8.6"))
        self.assertEqual(facts.cuda_arch_list_refusal, "")
        # A bad list is a report line, not a refusal of the report.
        gathered = doctor.gather(self.settings(COMFY_CUDA_ARCH_LIST="abc"))
        self.assertEqual(gathered.cuda_arch_list, "")
        self.assertRegex(gathered.cuda_arch_list_refusal, r"^COMFY_CUDA_ARCH_LIST='abc' is not a ;-separated list")
        self.assertTrue(facts.cdi_spec_present)
        self.assertIsInstance(facts.storage_free_kb, int)
        self.assertEqual(
            [(fact.place.key, fact.exists, fact.is_dir, fact.accessible) for fact in facts.storage],
            [
                ("COMFY_DATA_DIR", True, True, True),
                ("", False, False, False),
                ("COMFY_MODELS_DIR", True, True, True),
                ("COMFY_CACHE_DIR", True, True, True),
                ("COMFY_LOCAL_NODES_DIR", True, True, True),
            ],
        )
        self.assertEqual(facts.models_gid, (self.scratch.storage / "models").stat().st_gid)
        self.assertEqual((facts.host_model_gid, facts.host_model_gid_refusal), (str(os.getgid()), ""))
        self.assertEqual((facts.bind_address, facts.bind_refusal), ("127.0.0.1", ""))
        self.assertEqual((facts.port, facts.port_refusal), (self.scratch.port, ""))
        self.assertEqual(facts.port_error, "")
        self.assertFalse(facts.own_container_holds_port)

    def test_gather_records_what_nvidia_smi_and_the_settings_refuse(self) -> None:
        facts = doctor.gather(
            self.settings(
                COMFY_BIND_ADDRESS="localhost", COMFY_PORT="0", HOST_MODEL_GID="staff", FAKE_DOCKER_CDI_DIRS=""
            )
        )
        self.assertTrue(facts.nvidia_smi_present)
        self.assertEqual((facts.gpus, facts.gpu_error), ([], "exit status 1"))
        self.assertFalse(facts.cdi_spec_present)
        self.assertEqual((facts.bind_address, facts.bind_refusal), ("", env.BIND_NOT_ACCEPTED))
        self.assertEqual((facts.port, facts.port_refusal), ("", "COMFY_PORT requires a port from 1 to 65535"))
        self.assertEqual(facts.host_model_gid_refusal, "HOST_MODEL_GID must be a numeric group id")
        self.assertEqual(facts.port_error, "")
        facts = doctor.gather(self.settings(PATH=f"{self.scratch.tmp}/nowhere"))
        self.assertFalse(facts.nvidia_smi_present)
        self.assertEqual(facts.gpu_error, "nvidia-smi was not found")
        self.assertEqual(facts.engine, EngineFacts(name="docker"))
        self.assertIsNone(facts.storage_free_kb)

    def test_gather_reads_podman_cdi_specs_from_the_fixed_directories(self) -> None:
        present = self.scratch.tmp / "etc-cdi"
        (present / "nested").mkdir(parents=True)
        (present / "nested" / "spec.json").write_text('{"kind": "nvidia.com/gpu"}', encoding="utf-8")
        empty = self.scratch.tmp / "run-cdi"
        empty.mkdir()
        (empty / "other.yaml").write_text("kind: vendor.example/device\n", encoding="utf-8")
        missing = str(self.scratch.tmp / "missing")
        with mock.patch.object(doctor, "CDI_DIRECTORIES", (missing, str(empty))):
            self.assertFalse(doctor.gather(self.settings(CONTAINER_ENGINE="podman")).cdi_spec_present)
        with mock.patch.object(doctor, "CDI_DIRECTORIES", (missing, str(present))):
            facts = doctor.gather(self.settings(CONTAINER_ENGINE="podman"))
        self.assertTrue(facts.cdi_spec_present)
        assert facts.engine is not None
        self.assertEqual((facts.engine.name, facts.engine.rootless, facts.engine.runtime), ("podman", "true", "crun"))

    def test_gather_judges_the_port_with_the_bind_the_start_makes(self) -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = str(listener.getsockname()[1])
            self.assertIn("in use", doctor.gather(self.settings(COMFY_PORT=port)).port_error)
            running = {"COMFY_PORT": port, "FAKE_ENGINE_COMFY_RUNNING": "true"}
            self.assertTrue(doctor.gather(self.settings(**running)).own_container_holds_port)
            # Another port, another project, or a container without labels is not this project's service.
            for override in (
                {"FAKE_ENGINE_COMFY_PORT": "1"},
                {"FAKE_ENGINE_PROJECT": "other"},
                {"FAKE_ENGINE_NO_LABELS": "true"},
            ):
                with self.subTest(override=override):
                    self.assertFalse(doctor.gather(self.settings(**running, **override)).own_container_holds_port)
        self.assertEqual(doctor.gather(self.settings(COMFY_PORT=port)).port_error, "")
        # The cause is carried as the bind reported it, whatever it is.
        remote = self.settings(COMFY_BIND_ADDRESS="192.0.2.1", COMFY_ALLOW_REMOTE="true")
        self.assertIn("Cannot assign requested address", doctor.gather(remote).port_error)

    def test_gather_carries_a_disagreeing_container_engine_as_a_fact(self) -> None:
        engine.Engine("podman", self.settings()).record_stack()
        facts = doctor.gather(self.settings(CONTAINER_ENGINE="docker"))
        self.assertEqual(facts.engine_refusal, engine.stack_runs_on("podman", "docker"))
        assert facts.engine is not None
        self.assertEqual(facts.engine.name, "docker")
        facts = doctor.gather(self.settings(CONTAINER_ENGINE=None))
        self.assertEqual(facts.engine_refusal, "")
        self.assertEqual(facts.engine.name if facts.engine else None, "podman")

    def test_gather_reports_an_unsupported_engine_and_the_wsl_flag(self) -> None:
        with mock.patch.object(doctor, "running_under_wsl", return_value=True):
            facts = doctor.gather(self.settings(CONTAINER_ENGINE="lxc"))
        self.assertTrue(facts.wsl)
        self.assertEqual((facts.engine, facts.unsupported_engine), (None, "lxc"))
        self.assertFalse(facts.cdi_spec_present)
        self.assertIsNone(facts.storage_free_kb)

    def test_the_report_runs_end_to_end_on_the_scratch_host(self) -> None:
        settings = self.settings(FAKE_NVIDIA_SMI=SMI_LINE, FAKE_ENGINE_STORAGE_ROOT=str(self.scratch.tmp))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = doctor.run(settings, min_driver_major=MIN_DRIVER_MAJOR, allow_no_gpu=False, sage="off")
        lines = output.getvalue().splitlines()
        self.assertIn("[ok] NVIDIA GPU: NVIDIA GeForce RTX 5090, driver 615.78.08, compute capability 12.0", lines)
        self.assertIn("[ok] COMFY_CUDA_ARCH_LIST covers compute capability 12.0", lines)
        self.assertIn(
            "[warn] the managed custom-node directory does not exist yet: "
            f"{self.scratch.storage / 'data' / 'custom_nodes'} (bin/latentcrate init creates it)",
            lines,
        )
        self.assertIn(f"[ok] the local custom-node directory exists: {self.scratch.storage / 'local-nodes'}", lines)
        # The fake Docker lists no CDI directories, so the GPU path fails here.
        self.assertEqual(status, 1)
        self.assertTrue(any(line.startswith("[fail] Docker reports no CDI spec directories") for line in lines))

    def test_a_storage_path_that_is_a_file_fails_as_not_a_directory(self) -> None:
        local_nodes = self.scratch.tmp / "local-nodes-file"
        local_nodes.write_text("")
        settings = self.settings(COMFY_LOCAL_NODES_DIR=str(local_nodes))
        facts = doctor.gather(settings)
        self.assertEqual(
            [(fact.exists, fact.is_dir) for fact in facts.storage if fact.place.key == "COMFY_LOCAL_NODES_DIR"],
            [(True, False)],
        )
        lines = evaluate(facts, allow_no_gpu=True)
        self.assertIn(f"[fail] the local custom-node directory is not a directory: {local_nodes}", lines)


class HelperTests(unittest.TestCase):
    def test_parse_gpu_query_keeps_every_row_and_a_name_with_commas(self) -> None:
        output = f"{SMI_LINE}\n1, GPU-def, 8.6, 615.78.08, NVIDIA GeForce RTX 3090, Founders\n\n"
        self.assertEqual(
            doctor.parse_gpu_query(output),
            [RTX_5090, dataclasses.replace(RTX_3090, name="NVIDIA GeForce RTX 3090, Founders")],
        )
        self.assertEqual(doctor.parse_gpu_query(""), [])
        self.assertEqual(doctor.parse_gpu_query("0, GPU-x\n"), [GpuFacts("0", "GPU-x", "", "", "")])

    def test_selected_gpus_accepts_the_bare_selector(self) -> None:
        self.assertEqual(doctor.selected_gpus([RTX_5090, RTX_3090], "all"), [RTX_5090, RTX_3090])
        self.assertEqual(doctor.selected_gpus([RTX_5090, RTX_3090], "GPU-def"), [RTX_3090])
        self.assertEqual(doctor.selected_gpus([], "nvidia.com/gpu=all"), [])

    def test_format_finding_prefixes_everything_but_notes(self) -> None:
        self.assertEqual(doctor.format_finding(Finding("warn", "x")), "[warn] x")
        self.assertEqual(doctor.format_finding(Finding("note", "   y")), "   y")


if __name__ == "__main__":
    unittest.main()
