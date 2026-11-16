"""The ComfyUI service lifecycle: the requirement-file copy the image build
reads, the build, the start with its readiness wait, stop, status, logs,
and the GPU smoke test.

`up` and `build` hold the project lock from the requirement-file copy
through the build to the container start; `down`, `status`, and `logs`
take none, so a stop never waits for a build (`down` only stops and
removes by label). The stop grace period is `STOP_GRACE_PERIOD`, the
setting Compose applies, parsed here for the engine's `stop --time`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path, PurePosixPath

from . import LatentCrateError, warn
from .engine import PROJECT_LABEL, SERVICE, Engine
from .env import Settings
from .files import publish_tree
from .localhttp import http_get
from .nodes import require_selected_set
from .variants import Variant

# A container in one of these states will not answer (`restarting`: a crash
# loop under a restart policy; `removed`: the engine no longer knows it);
# anything else is polled again until the timeout.
STOPPED_STATES = ("exited", "dead", "stopped", "restarting", "removing", "removed")
CHECK_LOGS = "Check the logs: bash bin/latentcrate logs"
# The tree the Dockerfile COPYs into the node-deps-builder stage; the
# shipped `.gitkeep` keeps the directory in Git while its content is not.
REQUIREMENTS_DIR = "custom-node-requirements"
# A pip include line; the file it names is copied beside the including one.
INCLUDE_RE = re.compile(r"^\s*(?:-r|--requirement|-c|--constraint)(?:\s+|=)(?P<path>[^\s#]+)")
# A Compose duration the wrapper also needs as seconds: one unit, no mixing.
DURATION_RE = re.compile(r"^([0-9]+)([smh])$")
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}
SMOKE_SCRIPT = "/usr/local/bin/latentcrate-gpu-smoke"
# The workflow-set bind targets of compose.yaml, below the /data bind: a
# nested bind needs its mountpoint, which an engine would create owned by root.
WORKFLOW_MOUNTPOINTS = ("user/default/workflows/latentcrate", "user/default/workflows/latentcrate-local")


def grace_seconds(settings: Settings) -> int:
    """STOP_GRACE_PERIOD as the seconds `engine stop --time` takes."""
    value = settings.get("STOP_GRACE_PERIOD")
    match = DURATION_RE.fullmatch(value)
    if match is None:
        raise LatentCrateError(
            f"STOP_GRACE_PERIOD must be a number of seconds, minutes, or hours (30s, 1m), not {value!r}"
        )
    return int(match.group(1)) * UNIT_SECONDS[match.group(2)]


# --- The requirement-file copy ------------------------------------------------


def node_directories(managed: Path, local: Path) -> list[tuple[str, Path]]:
    """The custom-node directories of both roots, by case-folded name: a
    directory that is not hidden, not `*.disabled`, and not a symbolic link
    (the entrypoint refuses the first, ComfyUI skips the second, and a link
    would copy from outside the roots). A name both roots hold is refused
    as the entrypoint refuses it."""
    nodes: dict[str, tuple[str, Path]] = {}
    for root in (managed, local):
        for entry in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            if entry.is_symlink():
                raise LatentCrateError(
                    f"top-level custom-node symlinks are not allowed: {entry}; "
                    "place the checkout under COMFY_LOCAL_NODES_DIR"
                )
            if not entry.is_dir() or entry.name.endswith(".disabled") or entry.name == "__pycache__":
                continue
            if entry.name.startswith("."):
                raise LatentCrateError(
                    f"hidden custom-node directories are not supported: {entry}; rename it or suffix it with .disabled"
                )
            if (seen := nodes.get(entry.name.casefold())) is not None:
                raise LatentCrateError(
                    f"duplicate custom-node name in managed and local sources: {seen[0]} / {entry.name}"
                )
            nodes[entry.name.casefold()] = (entry.name, entry)
    return list(nodes.values())


def copy_requirement_file(source: Path, node: Path, destination: Path, copied: set[Path]) -> None:
    """Copy one requirement file of a node into the staging tree and, after
    it, the files its relative includes name. A file stays inside the node
    directory without symbolic links on its path, since the build reads
    the copy as that node's own."""
    path = Path(os.path.normpath(source))
    if path in copied:
        return
    if not path.is_relative_to(node) or os.path.realpath(path) != str(path):
        raise LatentCrateError(f"requirement include escapes its custom-node directory: {source}")
    if not path.is_file():
        raise LatentCrateError(f"referenced requirement file does not exist: {source}")
    target = destination / node.name / path.relative_to(node)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    copied.add(path)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if match := INCLUDE_RE.match(line):
            copy_requirement_file(path.parent / match.group("path"), node, destination, copied)


def copy_requirements(settings: Settings) -> None:
    """Copy every node's `requirements.txt` (with its includes) from the
    managed and local roots into `build/custom-node-requirements`, with
    `manifest.txt` naming the entry files, one `<node>/requirements.txt`
    line each in case-folded order. The tree is staged beside the
    destination and swapped in by two renames: `os.replace` refuses a
    non-empty directory, and the image build that reads the tree runs
    under the same lock as this copy, so the gap between the renames has
    no reader. Called under the project lock, so a staging tree an
    interrupted copy left behind, whatever its pid, is nobody's and is
    removed first; the managed root is created when `up` runs before
    `init`."""
    settings.managed_nodes_dir.mkdir(parents=True, exist_ok=True)
    if not settings.local_nodes_dir.is_dir():
        raise LatentCrateError(
            f"local custom-node directory does not exist: {settings.local_nodes_dir}; run: bash bin/latentcrate init"
        )
    destination = settings.build_dir / REQUIREMENTS_DIR
    staging = settings.build_dir / f".{REQUIREMENTS_DIR}.staging-{os.getpid()}"
    previous = settings.build_dir / f".{REQUIREMENTS_DIR}.backup"
    for leftover in (*settings.build_dir.glob(f".{REQUIREMENTS_DIR}.staging-*"), previous):
        if leftover.exists():
            shutil.rmtree(leftover)
    staging.mkdir(parents=True)
    try:
        entries: list[str] = []
        copied: set[Path] = set()
        for name, directory in node_directories(settings.managed_nodes_dir, settings.local_nodes_dir):
            requirements = directory / "requirements.txt"
            if not requirements.is_file():
                continue
            copy_requirement_file(requirements, directory, staging, copied)
            entries.append(f"{name}/requirements.txt")
        entries.sort(key=str.casefold)
        (staging / "manifest.txt").write_text("".join(f"{entry}\n" for entry in entries), encoding="utf-8")
        (staging / ".gitkeep").touch()
        publish_tree(staging, destination, previous)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


# --- Build and start ------------------------------------------------------------


def build(settings: Settings, engine: Engine, variant: Variant, pinned: dict[str, str]) -> None:
    """The requirement-file copy and the image build, under the caller's
    lock. The arch list is a build argument, exported through its
    validating read so a bad list is refused before the engine is called."""
    arch_list = settings.cuda_arch_list
    copy_requirements(settings)
    print("== Building the ComfyUI image ==")
    print("The first build can take a long time (possibly hours).")
    engine.compose(
        "build",
        SERVICE,
        gpu=True,
        frontend_dist=variant.dist_overlay,
        COMFY_CUDA_ARCH_LIST=arch_list,
        **variant.compose_env(pinned),
    )


def build_image(settings: Settings, engine: Engine, variant: Variant, pinned: dict[str, str]) -> None:
    with engine.lock("build"):
        build(settings, engine, variant, pinned)


def create_mountpoint(data_dir: Path, mountpoint: str) -> None:
    """Create a workflow-set mountpoint below the data directory one level at
    a time. The container writes the data directory, so a symbolic link on
    the way could make the wrapper create directories elsewhere on the host."""
    path = data_dir
    for part in PurePosixPath(mountpoint).parts:
        path = path / part
        if path.is_symlink():
            raise LatentCrateError(f"refusing a symbolic link where a workflow-set mountpoint goes: {path}")
        path.mkdir(exist_ok=True)


def start_stack(
    settings: Settings, engine: Engine, variant: Variant, pinned: dict[str, str], *, timeout: int, detach: bool
) -> int:
    """Build the image and recreate the ComfyUI container from it, wait
    until it answers, then follow its logs unless `detach`. Ctrl+C once
    the container is started (during the wait, with or without `detach`,
    or on the follow) stops it with the grace period and returns 130; the
    container stays for `down` to remove. The port is judged before the
    build so a taken one fails fast, and again under the lock, which is
    the judgement that counts."""
    engine.require_port(settings.port)
    with engine.lock("up"):
        require_selected_set(settings)
        build(settings, engine, variant, pinned)
        engine.require_port(settings.port)
        for mountpoint in WORKFLOW_MOUNTPOINTS:
            create_mountpoint(settings.data_dir, mountpoint)
        # Recreated unconditionally so Compose cannot keep a container
        # that still points at the previous image.
        engine.compose(
            "up",
            "--no-build",
            "--force-recreate",
            "--detach",
            SERVICE,
            gpu=True,
            frontend_dist=variant.dist_overlay,
            stdin=subprocess.DEVNULL,
            **variant.compose_env(pinned),
        )
        engine.record_stack()
    try:
        wait_until_ready(settings, engine, timeout)
        status = 0 if detach else follow_logs(engine)
    except KeyboardInterrupt:
        status = 130
    if status == 130:
        engine.command("stop", "--time", str(grace_seconds(settings)), comfy_container(engine, all_states=True))
    return status


def poll(state, timeout: int, *, stopped: str, timed_out: str, sleep=time.sleep, clock=time.monotonic):
    """Yield every two seconds while the container runs and the deadline has
    not passed; the caller returns from its loop body once it is ready.
    `state` reports the container's state; one of `STOPPED_STATES` raises
    `stopped` with `{state}` filled in, the deadline raises `timed_out`."""
    deadline = clock() + timeout
    while True:
        current = state()
        if current in STOPPED_STATES:
            raise LatentCrateError(stopped.format(state=current))
        yield
        if clock() >= deadline:
            raise LatentCrateError(timed_out)
        sleep(2)


def wait_until_ready(settings: Settings, engine: Engine, timeout: int, **poll_options) -> None:
    """Wait until ComfyUI answers GET / on the published address (the half
    of the container's health check the host can see). The container must
    stay running throughout; an exit or removal fails immediately."""
    # Looked up in every state: a container that exited between `up` and
    # this first poll is judged by the poll like any later exit.
    ids = engine.container_ids(SERVICE, all_states=True)
    container_id = ids[0] if ids else ""
    for _ in poll(
        lambda: engine.container_state(container_id) if container_id else "removed",
        timeout,
        stopped=f"container is {{state}} before it was ready. {CHECK_LOGS}",
        timed_out=f"container did not become healthy within {timeout}s. {CHECK_LOGS}",
        **poll_options,
    ):
        if http_get(f"{settings.service_url}/") is not None:
            print(f"LatentCrate is healthy at {settings.service_url}")
            return


# --- Stop, status, logs ---------------------------------------------------------


def stop_stack(engine: Engine, *, stop_seconds: int) -> None:
    """Remove every project-owned container, pod, and network by label,
    then the stack record, so the next `up` may choose its engine. No
    lock, no Compose render: a stop works with broken pins and never
    waits for a build."""
    owned = engine.containers()
    engine.remove_containers(owned, timeout=stop_seconds)
    pods = sorted({item["Pod"] for item in owned if item.get("Pod")})
    if engine.name == "podman" and pods:
        engine.command("pod", "rm", *pods)
    networks = engine.command(
        "network", "ls", "--quiet", "--filter", f"label={PROJECT_LABEL}={engine.settings.project_name}", capture=True
    ).stdout.split()
    if networks:
        engine.command("network", "rm", *networks)
    engine.forget_stack()


def print_containers(settings: Settings, engine: Engine) -> None:
    engine.command("ps", "--all", "--filter", f"label={PROJECT_LABEL}={settings.project_name}")


def comfy_container(engine: Engine, *, all_states: bool) -> str:
    """The one ComfyUI container of this Compose project (in every state
    with `all_states`, running otherwise)."""
    ids = engine.container_ids(SERVICE, all_states=all_states)
    if not ids:
        what = "does not exist" if all_states else "is not running"
        raise LatentCrateError(f"the ComfyUI container {what}; run: bash bin/latentcrate up")
    if len(ids) > 1:
        raise LatentCrateError(
            "multiple ComfyUI containers were found for this Compose project; run: bash bin/latentcrate down"
        )
    return ids[0]


def follow_logs(engine: Engine) -> int:
    """Follow the ComfyUI container's logs; the status of the follow, 130
    when Ctrl+C ended it."""
    container_id = comfy_container(engine, all_states=True)
    try:
        return engine.command("logs", "--follow", container_id, check=False).returncode
    except KeyboardInterrupt:
        return 130


# --- The GPU smoke test ---------------------------------------------------------


def require_running_image(engine: Engine, image: str) -> str:
    """The running ComfyUI container, which must run `image` (the selected
    variant), so the smoke test judges the image the flags name."""
    container_id = comfy_container(engine, all_states=False)
    expected = engine.command("image", "inspect", "--format", "{{.Id}}", image, capture=True, check=False)
    if expected.returncode != 0:
        raise LatentCrateError(f"selected image does not exist locally: {image}; run: bash bin/latentcrate build")
    actual = engine.command("inspect", "--format", "{{.Image}}", container_id, capture=True).stdout
    if actual.strip().removeprefix("sha256:") != expected.stdout.strip().removeprefix("sha256:"):
        raise LatentCrateError(
            f"the running container uses another image than {image}; "
            "rerun up with the same --sage and frontend selection (or run down first)"
        )
    return container_id


def smoke_gpu(settings: Settings, engine: Engine, variant: Variant, pinned: dict[str, str]) -> int:
    """Run the in-image GPU smoke test in the running container, streaming
    its output to the terminal and into `reports/gpu-<tag>-<time>.log`;
    the test's status."""
    image = variant.image(pinned)
    container_id = require_running_image(engine, image)
    require_sage = ["env", "LATENTCRATE_REQUIRE_SAGE=1"] if variant.sage != "off" else []
    reports = settings.root / "reports"
    reports.mkdir(exist_ok=True)
    report = reports / f"gpu-{image.rpartition(':')[2]}-{time.strftime('%Y%m%d-%H%M%S')}.log"
    # By container id rather than through Compose: the container is already
    # identified, so no render (and no pins beyond the tag) is needed.
    with (
        subprocess.Popen(
            [engine.name, "exec", container_id, *require_sage, SMOKE_SCRIPT],
            env=engine.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        ) as process,
        report.open("w", encoding="utf-8") as saved,
    ):
        for line in process.stdout:
            print(line, end="", flush=True)
            saved.write(line)
    print(f"Saved GPU report to {report}")
    if process.returncode:
        warn(f"the GPU smoke test failed with status {process.returncode}")
    return process.returncode
