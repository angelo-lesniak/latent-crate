"""The container engine: detection, Compose invocation, container lookup.

Podman is selected before Docker; `CONTAINER_ENGINE` fixes the choice. Podman
must be rootless and run crun (the keep-id mapping relies on it), and its
Compose provider must keep CDI device requests when a start wants the GPU
(the Docker Compose plugin drops them). Under WSL the engine client must be
the Linux one inside the distribution, not the Windows `.exe` on the
interop PATH.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import LatentCrateError, warn
from .env import Settings
from .files import write_atomic

ENGINES = ("podman", "docker")
# Both Docker Compose and podman-compose apply these labels.
PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"
# The one service of the Compose project and the port it listens on.
SERVICE = "comfy"
CONTAINER_PORT = "8188/tcp"


def publishes(container: dict, port: str) -> bool:
    """Whether the container publishes the host port; the host address is
    not compared because an engine reports `::1` or `0.0.0.0` for the same
    bind depending on how it was written."""
    ports = (container.get("NetworkSettings") or {}).get("Ports") or {}
    return any(str(bound.get("HostPort")) == port for bound in ports.get(CONTAINER_PORT) or [])


def _run(command: list[str], env: dict[str, str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, env=env, check=False, **kwargs)


def _refuse_on_failure(result: subprocess.CompletedProcess, capture: bool, summary: str) -> None:
    """One wording for a failed engine call: the summary, and the first
    line of stderr when it was captured."""
    if result.returncode == 0:
        return
    detail = (result.stderr or "").strip() if capture else ""
    raise LatentCrateError(summary + (f": {detail}" if detail else ""))


def run_tool(command: list[str], env: dict[str, str]) -> tuple[str | None, str]:
    """(stdout, cause): the stdout of a host tool and an empty cause, or None
    and the first line of what went wrong when the tool is missing, cannot
    be started, or fails."""
    if shutil.which(command[0], path=env.get("PATH")) is None:
        return None, f"{command[0]} was not found"
    try:
        result = _run(command, env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as error:
        return None, str(error)
    if result.returncode == 0:
        return result.stdout, ""
    cause = (result.stderr or "").strip().partition("\n")[0]
    return None, cause or f"exit status {result.returncode}"


def tool_output(command: list[str], env: dict[str, str]) -> str | None:
    """stdout of a host tool, or None when it did not succeed."""
    return run_tool(command, env)[0]


def compose_provider_version(name: str, env: dict[str, str]) -> str:
    """The Compose provider's version line. `podman compose version` prints
    Podman's own version first and the provider's line after it."""
    output = tool_output([name, "compose", "version"], env) or ""
    for line in output.splitlines():
        if "compose" in line.lower():
            return line.strip()
    return ""


def podman_compose_keeps_cdi(provider: str) -> bool:
    """`podman compose` can delegate to the Docker Compose plugin, which drops
    CDI `devices:` entries; the GPU path needs podman-compose. `provider` is
    the line `podman compose version` printed."""
    provider = provider.lower()
    return "docker-compose" not in provider and "docker compose" not in provider


def podman_rootless_crun(env: dict[str, str]) -> tuple[str, str]:
    """(rootless, runtime) as Podman reports them, or empty strings."""
    output = tool_output(["podman", "info", "--format", "{{.Host.Security.Rootless}}/{{.Host.OCIRuntime.Name}}"], env)
    if not output or "/" not in output:
        return "", ""
    rootless, _, runtime = output.strip().partition("/")
    return rootless, runtime


def storage_root(name: str, env: dict[str, str]) -> str:
    """The directory the engine stores images and layers in, where a build
    needs its free space; "/" when the engine names none or the path is not
    a directory on this host."""
    field = "{{.Store.GraphRoot}}" if name == "podman" else "{{.DockerRootDir}}"
    root = (tool_output([name, "info", "--format", field], env) or "").strip()
    return root if root and os.path.isdir(root) else "/"


def docker_cdi_spec_dirs(env: dict[str, str]) -> tuple[str, ...]:
    """The CDI specification directories the Docker daemon reads; empty when
    CDI is not enabled."""
    output = tool_output(["docker", "info", "--format", '{{range .CDISpecDirs}}{{.}}{{"\\n"}}{{end}}'], env) or ""
    return tuple(line.strip() for line in output.splitlines() if line.strip())


def running_under_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def windows_client(path: str) -> bool:
    """Under WSL, an engine executable named `.exe` is the Windows-side
    client on the interop PATH, which cannot run this project's stack."""
    return path.lower().endswith(".exe") and running_under_wsl()


def windows_client_refusal(name: str, path: str) -> str:
    return f"WSL must use a Linux {name} client inside the distribution, not {path}"


def port_refusal(address: str, port: str, cause: str) -> str:
    return f"cannot use host port {port} on {address}: {cause}"


@dataclass
class EngineFacts:
    """What the probes reported for one engine candidate; `doctor` prints
    them one finding each, `detect` accepts or refuses on them."""

    name: str
    present: bool = False
    # The executable `PATH` resolves the name to.
    path: str = ""
    windows_client: bool = False
    version_line: str = ""
    reachable: bool = False
    # The first line of what the engine said when it was not reachable.
    error: str = ""
    provider: str = ""
    # Docker: the daemon's version.
    server_version: str = ""
    rootless: str = ""
    runtime: str = ""
    keeps_cdi: bool = True
    storage_root: str = ""
    # Docker: where the daemon looks for CDI specifications; Podman's
    # directories are fixed and `doctor` knows them.
    cdi_spec_dirs: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return not unusable_reason(self)


def probes(facts: EngineFacts) -> list[tuple[bool, str, str]]:
    """The engine gate in probe order, one entry per probe: whether it
    passed, the finding when it passes, and the finding when it fails. The
    list ends after a failed probe the later ones depend on (no version
    without an executable, no provider without a reachable engine); the
    Podman rootless and crun probes are read together and both listed.
    `doctor` prints the entries and `detect` refuses with the first failed
    one, so the two cannot disagree."""
    name = facts.name
    rows = [
        (
            facts.present and not facts.windows_client,
            facts.version_line,
            windows_client_refusal(name, facts.path) if facts.windows_client else f"{name} was not found",
        )
    ]
    if not rows[0][0]:
        return rows
    cause = f": {facts.error}" if facts.error else ""
    rows.append((facts.reachable, f"{name} engine is reachable", f"{name} engine is not reachable{cause}"))
    if not facts.reachable:
        return rows
    rows.append((bool(facts.provider), facts.provider, f"{name} has no working Compose provider"))
    if name == "podman" and facts.provider:
        rows += [
            (
                facts.rootless == "true",
                "Podman engine is rootless",
                "the supported Podman path requires a rootless engine",
            ),
            (
                facts.runtime == "crun",
                "Podman uses crun, which supports keep-groups",
                f"rootless Podman with keep-groups requires crun (detected: {facts.runtime or 'unknown'})",
            ),
        ]
    return rows


def unusable_reason(facts: EngineFacts) -> str:
    """The first failed probe's finding, or an empty string for a usable engine."""
    return next((failed for passed, _, failed in probes(facts) if not passed), "")


def inspect_engine(name: str, env: dict[str, str]) -> EngineFacts:
    """Run the probes for one engine, stopping at the first missing piece."""
    facts = EngineFacts(name=name)
    facts.path = shutil.which(name, path=env.get("PATH")) or ""
    facts.present = bool(facts.path)
    facts.windows_client = windows_client(facts.path)
    if not facts.present or facts.windows_client:
        return facts
    facts.version_line = (tool_output([name, "--version"], env) or "").strip() or f"{name} is installed"
    info, facts.error = run_tool([name, "info"], env)
    facts.reachable = info is not None
    if not facts.reachable:
        return facts
    facts.provider = compose_provider_version(name, env)
    facts.storage_root = storage_root(name, env)
    if name == "docker":
        server = tool_output(["docker", "version", "--format", "{{.Server.Version}}"], env)
        facts.server_version = (server or "").strip()
        facts.cdi_spec_dirs = docker_cdi_spec_dirs(env)
    if name == "podman" and facts.provider:
        facts.rootless, facts.runtime = podman_rootless_crun(env)
        facts.keeps_cdi = podman_compose_keeps_cdi(facts.provider)
    return facts


@dataclass
class Engine:
    name: str
    settings: Settings

    @property
    def env(self) -> dict[str, str]:
        return self.settings.subprocess_env()

    def command(self, *args: str, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
        """Run an engine command (`podman ...` or `docker ...`)."""
        result = _run(
            [self.name, *args],
            self.env,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
        )
        if check:
            _refuse_on_failure(result, capture, f"{self.name} {args[0]} failed")
        return result

    def compose(
        self,
        *args: str,
        gpu: bool = True,
        frontend_dist: bool = False,
        capture: bool = False,
        check: bool = True,
        stdin: int | None = None,
        **extra: str,
    ) -> subprocess.CompletedProcess:
        """Run `engine compose` with the wrapper's file list, from the repository
        root: the service, the engine's overlay, the GPU device request unless
        the command runs without a GPU, and the dist-mode frontend mount.
        `stdin` is passed to the subprocess (`subprocess.DEVNULL` keeps a
        terminal the wrapper may not read from away from the container)."""
        files = ["--file", "compose.yaml", "--file", f"compose.{self.name}.yaml"]
        if gpu:
            files += ["--file", "compose.gpu.yaml"]
        if frontend_dist:
            files += ["--file", "compose.frontend-dist.yaml"]
        env = self.settings.subprocess_env(**extra)
        if self.name == "podman":
            # Podman's OCI output cannot retain Dockerfile SHELL metadata; keep
            # the Bash/pipefail build semantics for every Podman-backed provider.
            env["BUILDAH_FORMAT"] = "docker"
        result = _run(
            [self.name, "compose", *files, "--env-file", "pins.env", *args],
            env,
            cwd=self.settings.root,
            stdin=stdin,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
        )
        if check:
            _refuse_on_failure(
                result,
                capture,
                f"{self.name} compose {args[0] if args else ''} failed (status {result.returncode})",
            )
        return result

    @property
    def lock_file(self) -> Path:
        # One lock per project, whichever engine runs its stack (the stack
        # record below says which); the name is validated, so it is a safe
        # file name.
        return self.settings.build_dir / "locks" / f"{self.settings.project_name}.lock"

    @contextlib.contextmanager
    def lock(self, command: str = ""):
        """Serialize the steps that change this checkout's stack or the node
        sources its image is built from: `up` and `build` hold it from the
        requirement-file copy through the build to the container start,
        `nodes install|sync` hold it for their duration, and nobody else
        (`down`, `status`, `logs`, and the other `nodes` commands take
        none). The holder writes its PID and `command` into the lock file,
        so a second command can say whom it waits for before it blocks."""
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(self.lock_file, os.O_RDWR | os.O_CREAT, 0o644), "r+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pid, _, held_by = handle.read().strip().partition(" ")
                holder = (
                    f"`{held_by}` (pid {pid})" if held_by else f"pid {pid}" if pid else "another latentcrate command"
                )
                warn(f"waiting for {holder} to finish")
                fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()} {command}".rstrip() + "\n")
            handle.flush()
            yield

    @property
    def stack_record(self) -> Path:
        return stack_record_path(self.settings)

    def record_stack(self) -> None:
        """Remember which engine runs this project's stack, for every later
        command (`detect` reads it until `down` removes it)."""
        write_atomic(self.stack_record, json.dumps({"engine": self.name, "project": self.settings.project_name}) + "\n")

    def forget_stack(self) -> None:
        self.stack_record.unlink(missing_ok=True)

    def containers(self, *, all_states: bool = True) -> list[dict]:
        result = self.command(
            "ps",
            *(["--all"] if all_states else []),
            "--quiet",
            "--filter",
            f"label={PROJECT_LABEL}={self.settings.project_name}",
            capture=True,
        )
        found = []
        for cid in result.stdout.split():
            inspected = self.command("inspect", "--format", "{{json .}}", cid, capture=True, check=False)
            if inspected.returncode:
                # A `run --rm` container may disappear between ps and inspect.
                if self.container_missing(inspected.stderr):
                    continue
                raise LatentCrateError(f"could not inspect container {cid}: {(inspected.stderr or '').strip()}")
            found.append(json.loads(inspected.stdout))
        return found

    @staticmethod
    def container_missing(message: str | None) -> bool:
        return any(
            text in (message or "").lower() for text in ("no such container", "no such object", "no container with")
        )

    def remove_containers(self, owned: list[dict], *, timeout: int = 10) -> None:
        """Stop (with `timeout` seconds of grace, the engines' own default
        when none is given) and remove each container."""
        for item in owned:
            for action in ("stop", "rm"):
                deadline = time.monotonic() + 30
                while True:
                    grace = ["--time", str(timeout)] if action == "stop" else []
                    result = self.command(action, *grace, item["Id"], capture=True, check=False)
                    # Stopping a run --rm container also removes it. Docker
                    # may still be removing it when stop returns, so the
                    # `rm` is retried until that removal is done.
                    if not result.returncode or self.container_missing(result.stderr):
                        break
                    detail = (result.stderr or "").strip()
                    removing = "removal of container " in detail and "is already in progress" in detail
                    if not removing or time.monotonic() >= deadline:
                        raise LatentCrateError(f"{self.name} {action} failed: {detail}")
                    time.sleep(0.1)

    def own_service_publishes(self, port: str) -> bool:
        """Whether a running container of this project's own service
        publishes the host port."""
        return any(
            publishes(item, port) and ((item.get("Config") or {}).get("Labels") or {}).get(SERVICE_LABEL) == SERVICE
            for item in self.containers(all_states=False)
        )

    def require_port(self, port: str) -> None:
        """Accept an occupied port only when this project's own service
        publishes it; otherwise the port must bind on the configured address."""
        if self.own_service_publishes(port):
            return
        address = self.settings.bind_address
        if cause := bind_error(address, port):
            raise LatentCrateError(port_refusal(address, port, cause))

    def container_ids(self, service: str, *, all_states: bool = False) -> list[str]:
        """The running container(s) of one service of this Compose project
        (every state with all_states), asked from the engine directly
        through the standard Compose labels."""
        result = self.command(
            "ps",
            *(["--all"] if all_states else []),
            "--quiet",
            "--filter",
            f"label={PROJECT_LABEL}={self.settings.project_name}",
            "--filter",
            f"label={SERVICE_LABEL}={service}",
            capture=True,
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def container_state(self, container_id: str) -> str:
        """`.State.Status` of a container, by id or name (running, exited,
        ...), or `removed` when the engine no longer knows the container."""
        result = self.command("inspect", "--format", "{{.State.Status}}", container_id, capture=True, check=False)
        return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "removed"


def bind_error(address: str, port: str) -> str:
    """Bind the port on the configured address the way a publish will, and
    say what prevents it: an empty string when the port is free. `doctor`
    reports on it and `require_port` refuses on it, so the two cannot
    disagree."""
    host = address.strip("[]")
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family) as listener:
        # A recently closed connection in TIME_WAIT must not prevent restart.
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((host, int(port)))
        except OSError as error:
            return str(error)
    return ""


def stack_record_path(settings: Settings) -> Path:
    return settings.build_dir / "stack.json"


def recorded_engine(settings: Settings) -> str:
    """The engine `up` recorded for this project's stack, or an empty
    string when no stack record exists (or it belongs to another project)."""
    path = stack_record_path(settings)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    except (OSError, ValueError) as error:
        raise LatentCrateError(
            f"cannot read the stack record {path}: {error}; remove it if no stack is running"
        ) from error
    if not isinstance(record, dict) or record.get("project") != settings.project_name:
        return ""
    return str(record.get("engine") or "")


def requested_engine(settings: Settings) -> str:
    """The engine to probe: CONTAINER_ENGINE, or the one the running
    stack was started with when nothing is set; a setting that disagrees
    with the record is refused, because the other engine's stack would
    keep the GPU and the port."""
    requested = settings.get("CONTAINER_ENGINE")
    recorded = recorded_engine(settings)
    if recorded and requested and requested != recorded:
        raise LatentCrateError(stack_runs_on(recorded, requested))
    return requested or recorded


def stack_runs_on(recorded: str, requested: str) -> str:
    return (
        f"the stack runs on {recorded}, not {requested} (build/stack.json); "
        f"run: CONTAINER_ENGINE={recorded} bash bin/latentcrate down"
    )


# The wording of the engine gate, shared so that `detect`, which refuses,
# and `doctor`, which reports, cannot drift apart. `detect` adds the
# pointer to `doctor`; `doctor` is already the report.
NO_USABLE_ENGINE = "neither Podman nor Docker has both a reachable engine and Compose provider"
RUN_DOCTOR = "run: bash bin/latentcrate doctor"
COMPOSE_PLUGIN_DROPS_CDI = "the Podman compose provider is the Docker Compose plugin, which drops CDI GPU devices"
INSTALL_PODMAN_COMPOSE = "install podman-compose"


def unsupported_engine(name: str) -> str:
    return f"unsupported CONTAINER_ENGINE={name}"


@dataclass(frozen=True)
class Selection:
    """Which engine CONTAINER_ENGINE and the probes point at: the chosen
    facts, which are unusable when a named engine cannot run; the engines
    that are present but unusable, when nothing was named; and a name this
    project does not support. `detect` refuses on these and `doctor`
    reports them, from the one wording above."""

    chosen: EngineFacts | None = None
    candidates: tuple[EngineFacts, ...] = ()
    unsupported: str = ""


def select(env: dict[str, str], requested: str) -> Selection:
    """The requested engine wins and must be usable; otherwise the first
    usable of ENGINES, in order."""
    if requested:
        if requested not in ENGINES:
            return Selection(unsupported=requested)
        return Selection(chosen=inspect_engine(requested, env))
    present: list[EngineFacts] = []
    for candidate in ENGINES:
        facts = inspect_engine(candidate, env)
        if facts.usable:
            return Selection(chosen=facts)
        if facts.present:
            present.append(facts)
    return Selection(candidates=tuple(present))


def detect(settings: Settings, *, launching: bool = True, selection: Selection | None = None) -> Engine:
    """The engine every command uses; `selection` is what `doctor.gather`
    already probed, so `up` probes once. The stack record decides when
    CONTAINER_ENGINE is unset and refuses a setting that disagrees. A
    command that starts the service with the GPU (`launching`) needs a
    provider that keeps CDI devices; `templates` and a CPU render do not."""
    recorded = recorded_engine(settings)
    if selection is None:
        selection = select(settings.subprocess_env(), requested_engine(settings))
    if selection.unsupported:
        raise LatentCrateError(unsupported_engine(selection.unsupported))
    facts = selection.chosen
    if facts is None:
        raise LatentCrateError(f"{NO_USABLE_ENGINE}; {RUN_DOCTOR}")
    if recorded and facts.name != recorded:
        raise LatentCrateError(stack_runs_on(recorded, facts.name))
    if reason := unusable_reason(facts):
        raise LatentCrateError(f"{reason}; {RUN_DOCTOR}")
    if launching and facts.name == "podman" and not facts.keeps_cdi:
        raise LatentCrateError(f"{COMPOSE_PLUGIN_DROPS_CDI}; {INSTALL_PODMAN_COMPOSE}")
    return Engine(facts.name, settings)
