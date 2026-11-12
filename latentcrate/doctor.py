"""Host checks: the facts behind `doctor` and the judgement on them.

`gather` runs the probes (platform, engine, GPU, CDI, storage, port) and
returns :class:`HostFacts`; `evaluate` turns the facts into findings, one per
check, in the order the report prints them; `run` prints the report and
returns the exit status (1 when a `[fail]` line was printed). Keeping the
judgement free of subprocesses lets the tests build the facts by hand.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import LatentCrateError
from .engine import (
    COMPOSE_PLUGIN_DROPS_CDI,
    INSTALL_PODMAN_COMPOSE,
    NO_USABLE_ENGINE,
    Engine,
    EngineFacts,
    Selection,
    bind_error,
    port_refusal,
    probes,
    requested_engine,
    run_tool,
    running_under_wsl,
    select,
    unsupported_engine,
)
from .env import LOOPBACK, HostPath, Settings

# Where nvidia-ctk writes CDI specifications and where Podman reads them.
CDI_DIRECTORIES = ("/etc/cdi", "/var/run/cdi")
CDI_DEVICE = "nvidia.com/gpu"
CDI_GENERATE = "run: sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml"
CDI_NOTE = (
    "       CDI checks read configuration only; "
    "the first up and bash bin/latentcrate smoke-gpu prove device resolution."
)
# A full build with its caches measured about 35 GB on the validation host; two variants fit under 75.
BUILD_GB = 75
NVIDIA_SMI_QUERY = [
    "nvidia-smi",
    "--query-gpu=index,uuid,compute_cap,driver_version,name",
    "--format=csv,noheader",
]
WSL_PORT_NOTE = (
    "; under WSL2 a Windows-side program can hold a port invisibly to Linux tools "
    "(check with netstat -ano from Windows)"
)
NOT_LINUX = "run LatentCrate in a Linux environment (native Linux or WSL2), not directly from Windows"


@dataclass(frozen=True)
class GpuFacts:
    """One row of nvidia-smi's answer."""

    index: str
    uuid: str
    compute_cap: str
    driver_version: str
    name: str


@dataclass(frozen=True)
class StorageFact:
    """A storage entry the wrapper mounts or creates: whether it exists, is
    a directory, and is writable or, for a read-only mount, readable."""

    place: HostPath
    exists: bool
    is_dir: bool
    accessible: bool


@dataclass
class HostFacts:
    """Everything `evaluate` judges; `gather` fills it from the host and
    the settings."""

    sysname: str = ""
    machine: str = ""
    wsl: bool = False
    # Why CONTAINER_ENGINE was refused against the stack record, if it was.
    engine_refusal: str = ""
    engine: EngineFacts | None = None
    # Present engines when none is usable, reported one block each.
    candidates: list[EngineFacts] = field(default_factory=list)
    # A CONTAINER_ENGINE value that names no supported engine.
    unsupported_engine: str | None = None
    nvidia_smi_present: bool = False
    gpus: list[GpuFacts] = field(default_factory=list)
    # What nvidia-smi said when it gave no answer, as `EngineFacts.error`.
    gpu_error: str = ""
    gpu_device: str = "nvidia.com/gpu=all"
    cuda_arch_list: str = ""
    # Whether a CDI spec for nvidia.com/gpu exists where the engine reads them.
    cdi_spec_present: bool = False
    # Free space on the engine's storage root; None when it could not be measured.
    storage_free_kb: int | None = None
    storage: list[StorageFact] = field(default_factory=list)
    # The models directory's group when it exists.
    models_gid: int | None = None
    host_model_gid: str = ""
    host_model_gid_refusal: str = ""
    bind_address: str = ""
    bind_refusal: str = ""
    port: str = ""
    port_refusal: str = ""
    # What prevents binding the port on the bind address; empty when it is free.
    port_error: str = ""
    own_container_holds_port: bool = False

    @property
    def selection(self) -> Selection:
        """The engine selection as `engine.detect` takes it, so `up` can
        reuse what `doctor` probed instead of probing the engine again."""
        return Selection(
            chosen=self.engine, candidates=tuple(self.candidates), unsupported=self.unsupported_engine or ""
        )


@dataclass
class Finding:
    """One report line; `note` lines print without a prefix and are not counted."""

    level: str
    message: str


def format_finding(finding: Finding) -> str:
    return finding.message if finding.level == "note" else f"[{finding.level}] {finding.message}"


# --- probes -----------------------------------------------------------------


def parse_gpu_query(output: str) -> list[GpuFacts]:
    """nvidia-smi's CSV rows: index, UUID, compute capability, driver, name
    (which may contain commas)."""
    gpus = []
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = [part.strip() for part in line.split(",", 4)] + [""] * 4
        gpus.append(GpuFacts(*fields[:5]))
    return gpus


def cdi_spec_present(directories: tuple[str, ...]) -> bool:
    """Whether a file below one of the directories names the NVIDIA CDI device."""
    for directory in directories:
        for dirpath, _, filenames in os.walk(directory):
            for filename in filenames:
                try:
                    text = Path(dirpath, filename).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if CDI_DEVICE in text:
                    return True
    return False


def _storage_facts(settings: Settings) -> list[StorageFact]:
    """The storage entries the wrapper mounts or `init` creates: a read-only
    mount needs to be readable, the others writable."""
    facts = []
    for place in settings.storage:
        if place.mounted or place.created_by_init:
            mode = os.W_OK if place.writable else os.R_OK | os.X_OK
            path = place.path
            facts.append(StorageFact(place, path.exists(), path.is_dir(), os.access(path, mode)))
    return facts


def _free_kb(root: str) -> int | None:
    try:
        return shutil.disk_usage(root).free // 1024
    except OSError:
        return None


def _own_container_holds_port(engine: EngineFacts | None, settings: Settings, port: str) -> bool:
    """Whether this project's own service publishes the requested port."""
    if engine is None or not engine.reachable or not port:
        return False
    try:
        return Engine(engine.name, settings).own_service_publishes(port)
    except (LatentCrateError, OSError):
        return False


def _setting(read, default: str = "") -> tuple[str, str]:
    """(value, refusal) of a validating property, so a refused setting is
    reported in the report instead of stopping it."""
    try:
        return read(), ""
    except LatentCrateError as error:
        return default, str(error)


def gather(settings: Settings) -> HostFacts:
    """Run every probe. The engine comes from `engine.select`, the rule `up`
    applies through `engine.detect`: the requested or recorded engine, or
    the first usable one. What `detect` refuses on, this records, so the
    two cannot disagree."""
    env = settings.subprocess_env()
    uname = os.uname()
    try:
        requested = requested_engine(settings)
        engine_refusal = ""
    except LatentCrateError as error:
        # The report describes the engine the setting names; `up` refuses on the message.
        requested, engine_refusal = settings.get("CONTAINER_ENGINE"), str(error)
    selection = select(env, requested)
    engine = selection.chosen
    gpu_output, gpu_error = run_tool(NVIDIA_SMI_QUERY, env)
    bind_address, bind_refusal = _setting(lambda: settings.bind_address)
    port, port_refusal = _setting(lambda: settings.port)
    host_model_gid, host_model_gid_refusal = _setting(lambda: settings.host_model_gid)
    facts = HostFacts(
        sysname=uname.sysname,
        machine=uname.machine,
        wsl=running_under_wsl(),
        engine_refusal=engine_refusal,
        engine=engine,
        candidates=list(selection.candidates),
        unsupported_engine=selection.unsupported or None,
        nvidia_smi_present=shutil.which("nvidia-smi", path=env.get("PATH")) is not None,
        gpus=parse_gpu_query(gpu_output or ""),
        gpu_error=gpu_error,
        gpu_device=settings.gpu_device,
        cuda_arch_list=settings.cuda_arch_list,
        storage=_storage_facts(settings),
        host_model_gid=host_model_gid,
        host_model_gid_refusal=host_model_gid_refusal,
        bind_address=bind_address,
        bind_refusal=bind_refusal,
        port=port,
        port_refusal=port_refusal,
        own_container_holds_port=_own_container_holds_port(engine, settings, port),
    )
    if engine is not None and engine.reachable:
        facts.storage_free_kb = _free_kb(engine.storage_root or "/")
        directories = CDI_DIRECTORIES if engine.name == "podman" else engine.cdi_spec_dirs
        facts.cdi_spec_present = cdi_spec_present(directories)
    if settings.models_dir.is_dir():
        facts.models_gid = settings.models_dir.stat().st_gid
    if bind_address and port:
        facts.port_error = bind_error(bind_address, port)
    return facts


# --- judgement --------------------------------------------------------------


def _check(condition: bool, good: str, bad: str, level: str = "fail") -> Finding:
    return Finding("ok", good) if condition else Finding(level, bad)


def _gpu_problem(message: str, allow_no_gpu: bool) -> Finding:
    """A GPU problem stops `up` unless --allow-no-gpu turned it into a warning."""
    if allow_no_gpu:
        return Finding("warn", f"{message} (--allow-no-gpu was supplied)")
    return Finding("fail", message)


def _platform_findings(facts: HostFacts) -> list[Finding]:
    if facts.sysname != "Linux":
        return [Finding("fail", NOT_LINUX)]
    findings = [
        _check(
            facts.machine == "x86_64",
            "Linux on x86-64",
            f"unsupported host architecture: {facts.machine}; the pinned CUDA image is x86-64 only",
        )
    ]
    if facts.wsl:
        findings.append(
            Finding(
                "warn",
                "WSL detected; WSL2 is not validated, and docs/validation.md#known-gaps lists what a run must show",
            )
        )
    return findings


def _engine_findings(engine: EngineFacts, allow_no_gpu: bool) -> list[Finding]:
    """The gate engine.detect applies, one line per probe, and for Podman
    the Compose provider's CDI handling."""
    findings = [_check(passed, good, bad) for passed, good, bad in probes(engine)]
    if engine.name != "podman" or not (engine.reachable and engine.provider):
        return findings
    if engine.keeps_cdi:
        findings.append(Finding("ok", "the Podman compose provider keeps CDI device requests"))
    else:
        findings.append(_gpu_problem(f"{COMPOSE_PLUGIN_DROPS_CDI}; {INSTALL_PODMAN_COMPOSE}", allow_no_gpu))
    return findings


def _engine_selection_findings(facts: HostFacts, allow_no_gpu: bool) -> list[Finding]:
    findings = [Finding("fail", facts.engine_refusal)] if facts.engine_refusal else []
    if facts.unsupported_engine is not None:
        return [*findings, Finding("fail", unsupported_engine(facts.unsupported_engine))]
    if facts.engine is None:
        findings.append(Finding("fail", NO_USABLE_ENGINE))
        for candidate in facts.candidates:
            findings += _engine_findings(candidate, allow_no_gpu)
        return findings
    return findings + _engine_findings(facts.engine, allow_no_gpu)


def _storage_root_findings(facts: HostFacts) -> list[Finding]:
    if facts.engine is None or not facts.engine.reachable:
        return []
    root = facts.engine.storage_root or "/"
    if facts.storage_free_kb is None:
        return [
            Finding(
                "warn",
                f"could not measure free disk space for engine storage at {root}; "
                f"ensure about {BUILD_GB} GB are free before building",
            )
        ]
    free_gb = facts.storage_free_kb // 1024 // 1024
    return [
        _check(
            free_gb >= BUILD_GB,
            f"engine storage at {root} has {free_gb} GB free",
            f"engine storage at {root} has only {free_gb} GB free; a full LatentCrate build needs about {BUILD_GB} GB",
            level="warn",
        )
    ]


def _major(version: str) -> int | None:
    major = version.partition(".")[0]
    return int(major) if re.fullmatch(r"[0-9]+", major) else None


def arch_list_covers(arch_list: str, capability: str) -> bool:
    """Whether COMFY_CUDA_ARCH_LIST (`;` or whitespace separated, `+PTX`
    allowed) names the compute capability exactly."""
    return capability in {entry.removesuffix("+PTX") for entry in arch_list.replace(";", " ").split()}


def selected_gpus(gpus: list[GpuFacts], device: str) -> list[GpuFacts]:
    """The GPUs GPU_DEVICE names: every GPU for `all`, else the one with that
    nvidia-smi index or UUID; empty when nothing matches."""
    selector = device.removeprefix(f"{CDI_DEVICE}=")
    if selector == "all":
        return list(gpus)
    return [gpu for gpu in gpus if selector in (gpu.index, gpu.uuid)]


def _gpu_findings(facts: HostFacts, min_driver_major: int, allow_no_gpu: bool) -> list[Finding]:
    if not facts.nvidia_smi_present:
        return [_gpu_problem("nvidia-smi was not found; the supported GPU path is unavailable", allow_no_gpu)]
    if not facts.gpus:
        cause = f": {facts.gpu_error}" if facts.gpu_error else ""
        return [_gpu_problem(f"nvidia-smi is installed but could not query a GPU{cause}", allow_no_gpu)]
    first = facts.gpus[0]
    driver = first.driver_version
    findings = [Finding("ok", f"NVIDIA GPU: {first.name}, driver {driver}, compute capability {first.compute_cap}")]
    major = _major(driver)
    if major is not None and major >= min_driver_major:
        findings.append(Finding("ok", f"NVIDIA driver {driver} satisfies the pinned minimum major {min_driver_major}"))
    else:
        findings.append(
            _gpu_problem(
                f"NVIDIA driver {driver} is older than the pinned minimum major {min_driver_major} "
                "(CUDA_MIN_DRIVER_MAJOR)",
                allow_no_gpu,
            )
        )
    selected = selected_gpus(facts.gpus, facts.gpu_device)
    if not selected:
        findings.append(
            Finding(
                "warn",
                f"could not map GPU_DEVICE={facts.gpu_device} to an nvidia-smi index or GPU UUID; checking every GPU",
            )
        )
        selected = facts.gpus
    for gpu in selected:
        if arch_list_covers(facts.cuda_arch_list, gpu.compute_cap):
            findings.append(Finding("ok", f"COMFY_CUDA_ARCH_LIST covers compute capability {gpu.compute_cap}"))
        else:
            findings.append(
                _gpu_problem(
                    f"COMFY_CUDA_ARCH_LIST={facts.cuda_arch_list} does not cover compute capability "
                    f"{gpu.compute_cap}; add it to the list in .env",
                    allow_no_gpu,
                )
            )
    return findings


def _cdi_findings(facts: HostFacts, allow_no_gpu: bool) -> list[Finding]:
    engine = facts.engine
    if engine is None or not engine.reachable:
        return []
    if engine.name == "podman":
        where = f"under {' or '.join(CDI_DIRECTORIES)}"
    else:
        where = f"in: {' '.join(engine.cdi_spec_dirs)}"
    if engine.name == "docker" and not engine.cdi_spec_dirs:
        finding = _gpu_problem(
            'Docker reports no CDI spec directories; enable CDI in /etc/docker/daemon.json ("features": '
            f'{{"cdi": true}}) or upgrade Docker so {facts.gpu_device} can resolve',
            allow_no_gpu,
        )
    elif facts.cdi_spec_present:
        finding = Finding("ok", f"a CDI spec for {CDI_DEVICE} exists {where}")
    else:
        finding = _gpu_problem(f"no {CDI_DEVICE} CDI spec was found {where}; {CDI_GENERATE}", allow_no_gpu)
    return [finding, Finding("note", CDI_NOTE)]


def _storage_findings(facts: HostFacts) -> list[Finding]:
    findings = []
    for fact in facts.storage:
        label, path = fact.place.label, fact.place.path
        if not fact.exists:
            findings.append(Finding("warn", f"{label} does not exist yet: {path} (bin/latentcrate init creates it)"))
            continue
        if not fact.is_dir:
            findings.append(Finding("fail", f"{label} is not a directory: {path}"))
            continue
        access = "writable" if fact.place.writable else "readable"
        findings.append(_check(fact.accessible, f"{label} exists: {path}", f"{label} is not {access}: {path}"))
    return findings


def _model_gid_findings(facts: HostFacts) -> list[Finding]:
    if facts.engine is None or facts.engine.name != "docker" or facts.models_gid is None:
        return []
    if facts.host_model_gid_refusal:
        return [Finding("fail", facts.host_model_gid_refusal)]
    if str(facts.models_gid) != facts.host_model_gid:
        return [
            Finding(
                "warn",
                f"the models directory's group is {facts.models_gid} but HOST_MODEL_GID={facts.host_model_gid}; "
                "Docker may lack group access",
            )
        ]
    return []


def _network_findings(facts: HostFacts) -> list[Finding]:
    findings = []
    if facts.bind_refusal:
        findings.append(Finding("fail", facts.bind_refusal))
    elif facts.bind_address not in LOOPBACK:
        findings.append(Finding("warn", f"ComfyUI will bind beyond localhost: {facts.bind_address}"))
    if facts.port_refusal:
        findings.append(Finding("fail", facts.port_refusal))
    elif facts.port_error and not facts.own_container_holds_port:
        note = WSL_PORT_NOTE if facts.wsl else ""
        findings.append(Finding("fail", port_refusal(facts.bind_address, facts.port, facts.port_error) + note))
    return findings


def evaluate(facts: HostFacts, *, min_driver_major: int, allow_no_gpu: bool, sage: str) -> list[Finding]:
    """Every check in report order. A GPU problem is a failure unless
    --allow-no-gpu was supplied, which turns it into a warning."""
    if sage != "off":
        findings = [Finding("ok", "checking the SageAttention-capable image")]
    else:
        findings = [Finding("ok", "checking the smaller image without SageAttention")]
    findings += _platform_findings(facts)
    findings += _engine_selection_findings(facts, allow_no_gpu)
    findings += _storage_root_findings(facts)
    findings += _gpu_findings(facts, min_driver_major, allow_no_gpu)
    findings += _cdi_findings(facts, allow_no_gpu)
    findings += _storage_findings(facts)
    findings += _model_gid_findings(facts)
    findings += _network_findings(facts)
    return findings


def run(
    settings: Settings,
    *,
    min_driver_major: int,
    allow_no_gpu: bool,
    sage: str,
    facts: HostFacts | None = None,
) -> int:
    """Print the doctor report; 0 when no check failed, else 1. `up` passes
    the facts it gathered, so it probes the engine once."""
    print("== LatentCrate host doctor ==", flush=True)
    if facts is None:
        facts = gather(settings)
    findings = evaluate(facts, min_driver_major=min_driver_major, allow_no_gpu=allow_no_gpu, sage=sage)
    for finding in findings:
        print(format_finding(finding))
    failure_count = sum(1 for finding in findings if finding.level == "fail")
    warning_count = sum(1 for finding in findings if finding.level == "warn")
    print(f"\nDoctor completed with {failure_count} failure(s) and {warning_count} warning(s).", flush=True)
    return 0 if failure_count == 0 else 1
