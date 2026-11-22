"""Install or inspect commit-pinned sets of public ComfyUI third-party
nodes in the managed custom-node directory, and export one from any
ComfyUI installation.

The CLI runs this module in-process on the host: ``nodes install`` and
``nodes sync`` call :func:`install` (which takes the project lock and
refuses while ComfyUI runs), ``nodes status`` calls :func:`status`,
``nodes check`` calls :func:`check`, ``nodes export`` prints the text
:func:`export` returns, and ``up`` calls :func:`require_recorded_sets`.
Each node is cloned into a staging sibling and renamed into place, so a
node directory is either absent or complete; every set an install
chose is a line of the record file :data:`RECORD_NAME`, written before
the first clone.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import LatentCrateError, warn
from .engine import SERVICE, Engine
from .env import Settings
from .files import read_utf8, write_atomic
from .names import COMMIT_RE, SET_NAME_RE, load_catalog, read_toml
from .names import description as read_description
from .variants import git_environment, run_git

# One line per installed set, in install order, written by install or sync
# before the first clone; ComfyUI loads `.py` files only, so the file is inert.
RECORD_NAME = ".latentcrate-node-set"
# A clone is staged beside its destination under this suffix: not hidden
# (the entrypoint refuses hidden directories) and skipped by ComfyUI and
# the entrypoint alike.
STAGING_SUFFIX = ".latentcrate-clone.disabled"
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
REPOSITORY_PATH_RE = re.compile(r"^/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+(?:\.git)?$")
# The SSH spellings of a GitHub-style origin an export rewrites to HTTPS.
SSH_ORIGIN_RE = re.compile(r"^(?:git@([^:/\s]+):|ssh://git@([^/\s]+)/)([^\s]+)$")
# Local Git settings that run programs or redirect fetches; a checkout
# carrying one is never inspected or exported.
UNSAFE_CONFIG_RE = (
    r"^(filter\.|diff\..*\.(command|textconv)$|core\.(fsmonitor|hookspath|attributesfile|sshcommand)$"
    r"|include\.|includeif\.|url\.|credential\.|http\.)"
)
# The states `node_state` reports; `ready` is the only one `up` and a
# successful install accept.
READY = "ready"
DIRTY_QUERY = ("status", "--porcelain", "--untracked-files=all")
EXPORT_DESCRIPTION = "Third-party nodes exported from an existing ComfyUI installation"


@dataclass(frozen=True)
class Node:
    name: str
    repository: str
    commit: str


@dataclass(frozen=True)
class NodeSet:
    name: str
    description: str
    nodes: tuple[Node, ...]


# --- Manifests ---------------------------------------------------------------


def load_allowed_hosts(config_dir: Path) -> set[str]:
    path = config_dir / "custom-nodes" / "allowed-git-hosts.txt"
    try:
        lines = read_utf8(path).splitlines()
    except OSError as error:
        raise LatentCrateError(f"could not read allowed Git hosts: {error}") from error
    hosts = {line.strip().lower() for line in lines if line.strip() and not line.lstrip().startswith("#")}
    if not hosts or any(not HOST_RE.fullmatch(host) for host in hosts):
        raise LatentCrateError(f"allowed Git hosts must contain valid lowercase host names: {path}")
    return hosts


def public_repository(value: object) -> str | None:
    """`value` as `https://<host>/<owner>/<repository>` with the host
    lower-cased and `.git` dropped, or None when it is not a credential-free
    HTTPS owner/repository URL."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or not REPOSITORY_PATH_RE.fullmatch(parsed.path)
    ):
        return None
    path = parsed.path[:-4] if parsed.path.lower().endswith(".git") else parsed.path
    return f"https://{parsed.hostname.lower()}{path}"


def allowed_repository(value: object, allowed_hosts: set[str]) -> str | None:
    """`public_repository` on an allowed host, or None."""
    repository = public_repository(value)
    if repository is None or urlsplit(repository).hostname not in allowed_hosts:
        return None
    return repository


def read_manifest(path: Path, allowed_hosts: set[str]) -> NodeSet:
    name = path.stem
    if not SET_NAME_RE.fullmatch(name) or name == "all":
        raise LatentCrateError(f"unsafe custom-node set filename: {path.name}")
    document = read_toml(path, {"schema", "description", "node"}, name)
    description = read_description(document, name)
    raw_nodes = document.get("node")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise LatentCrateError(f"{name}: at least one [[node]] entry is required")
    nodes: list[Node] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_nodes, start=1):
        context = f"{name} node {index}"
        if not isinstance(raw, dict) or set(raw) != {"name", "repository", "commit"}:
            raise LatentCrateError(f"{context}: must contain exactly name, repository, and commit")
        node_name, commit = raw["name"], raw["commit"]
        # A `.disabled` directory is one ComfyUI never loads.
        if not isinstance(node_name, str) or not SET_NAME_RE.fullmatch(node_name) or node_name.endswith(".disabled"):
            raise LatentCrateError(f"{context}: unsafe custom-node directory name: {node_name!r}")
        if node_name.casefold() in seen:
            raise LatentCrateError(f"{name}: duplicate custom-node name: {node_name}")
        seen.add(node_name.casefold())
        repository = allowed_repository(raw["repository"], allowed_hosts)
        if repository is None:
            raise LatentCrateError(
                f"{context}: repository must be a credential-free HTTPS owner/repository URL "
                "on a host listed in allowed-git-hosts.txt"
            )
        if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
            raise LatentCrateError(f"{context}: commit must be a full 40-character Git commit in lowercase")
        nodes.append(Node(node_name, repository, commit))
    return NodeSet(name, description, tuple(nodes))


def catalog(config_dir: Path, allowed_hosts: set[str]) -> dict[str, NodeSet]:
    """The shipped custom-node sets below config_dir, keyed by name."""
    sets = config_dir / "custom-nodes" / "sets"
    return load_catalog(sets, lambda path: read_manifest(path, allowed_hosts), "custom-node set")


def merged_nodes(node_sets: list[NodeSet], record: Path, recorded: Collection[str]) -> list[Node]:
    """Every node of the sets once, by case-folded directory name, in the
    first set's spelling; two sets that pin one directory to a different
    repository (case ignored, as hosting services do) or commit are refused
    together. A refusal involving a set in `recorded` names the line of
    `record` to delete."""
    by_name: dict[str, tuple[str, Node]] = {}
    for node_set in node_sets:
        for node in node_set.nodes:
            first, previous = by_name.setdefault(node.name.casefold(), (node_set.name, node))
            if previous.repository.lower() != node.repository.lower():
                difference = "repositories"
            elif previous.commit != node.commit:
                difference = "commits"
            else:
                continue
            message = f"custom-node sets {first} and {node_set.name} pin {node.name} to different {difference}; "
            lines = [name for name in (first, node_set.name) if name in recorded]
            if lines:
                message += f"delete the line {' or '.join(lines)} from {record}, or fix the manifests"
            else:
                message += "use one of them or fix the manifests"
            raise LatentCrateError(message)
    return [node for _, node in by_name.values()]


# --- The record and the `up` check ------------------------------------------


def recorded_sets(managed_dir: Path) -> list[str]:
    """The set names the record file holds, one per line; none without one."""
    record = managed_dir / RECORD_NAME
    try:
        lines = read_utf8(record).splitlines()
    except FileNotFoundError:
        return []
    names = [line.strip() for line in lines if line.strip()]
    for name in names:
        if not SET_NAME_RE.fullmatch(name):
            raise LatentCrateError(f"{record} holds {name!r}, which is not a custom-node set name; remove that line")
    return list(dict.fromkeys(names))


def recorded_node_sets(settings: Settings) -> list[NodeSet]:
    """The manifests of the recorded sets, in record order."""
    managed = settings.managed_nodes_dir
    names = recorded_sets(managed)
    if not names:
        return []
    sets = catalog(settings.config_dir, load_allowed_hosts(settings.config_dir))
    for name in names:
        if name not in sets:
            raise LatentCrateError(f"{managed / RECORD_NAME} names an unknown custom-node set {name}; remove that line")
    return [sets[name] for name in names]


def selected_nodes(settings: Settings, selected: list[NodeSet]) -> list[Node]:
    """The selected sets' nodes once each, merged after the recorded sets,
    so a directory keeps the spelling it was installed under."""
    recorded = recorded_node_sets(settings)
    record = settings.managed_nodes_dir / RECORD_NAME
    merged = merged_nodes([*recorded, *selected], record, [node_set.name for node_set in recorded])
    wanted = {node.name.casefold() for node_set in selected for node in node_set.nodes}
    return [node for node in merged if node.name.casefold() in wanted]


def require_recorded_sets(settings: Settings) -> None:
    """Refuse a start while the recorded sets disagree about a node or a
    node directory of one of them is missing, by one stat each, so a
    half-installed set never starts; no record, no check."""
    managed = settings.managed_nodes_dir
    node_sets = recorded_node_sets(settings)
    names = [node_set.name for node_set in node_sets]
    directories = {node.name.casefold(): node.name for node in merged_nodes(node_sets, managed / RECORD_NAME, names)}
    missing = {
        node_set.name: [
            directories[node.name.casefold()]
            for node in node_set.nodes
            if not os.path.lexists(managed / directories[node.name.casefold()])
        ]
        for node_set in node_sets
    }
    incomplete = [name for name, absent in missing.items() if absent]
    if incomplete:
        raise LatentCrateError(
            "; ".join(f"custom-node set {name} is missing {', '.join(missing[name])}" for name in incomplete)
            + f"; run: bash bin/latentcrate nodes install {' '.join(incomplete)}"
        )


# --- Checkouts ---------------------------------------------------------------


def checkout_git(path: Path, *arguments: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    """Hardened Git on the checkout at `path`, addressed explicitly so no
    parent repository is consulted; the caller reads the status."""
    return run_git(f"--git-dir={path / '.git'}", f"--work-tree={path}", *arguments, env=env)


def node_git(path: Path, *arguments: str, env: dict[str, str]) -> str:
    """stdout of a `checkout_git` command that must succeed."""
    result = checkout_git(path, *arguments, env=env)
    if result.returncode != 0:
        detail = (result.stderr or "").strip().partition("\n")[0]
        raise LatentCrateError(f"git {arguments[0]} failed in {path}: {detail}")
    return result.stdout.strip()


def is_checkout(path: Path) -> bool:
    """A regular directory holding a regular `.git` directory."""
    git_dir = path / ".git"
    return path.is_dir() and not path.is_symlink() and git_dir.is_dir() and not git_dir.is_symlink()


def unsafe_local_config(path: Path, env: dict[str, str]) -> bool:
    """Whether the checkout's own configuration matches UNSAFE_CONFIG_RE."""
    result = checkout_git(
        path, "config", "--local", "--no-includes", "--name-only", "--get-regexp", UNSAFE_CONFIG_RE, env=env
    )
    if result.returncode not in (0, 1):
        raise LatentCrateError(
            f"could not inspect the local Git configuration of {path}: {(result.stderr or '').strip()}"
        )
    return result.returncode == 0


def node_state(path: Path, node: Node, env: dict[str, str]) -> str:
    """missing, conflict (a symbolic link or not a Git checkout),
    different-repository, dirty, different-commit, or ready."""
    if not os.path.lexists(path):
        return "missing"
    if not is_checkout(path):
        return "conflict"
    if unsafe_local_config(path, env):
        raise LatentCrateError(f"refusing execution-capable local Git configuration in {path}")
    origin = node_git(path, "remote", "get-url", "origin", env=env)
    head = node_git(path, "rev-parse", "HEAD", env=env).lower()
    dirty = bool(node_git(path, *DIRTY_QUERY, env=env))
    # Hosting services match repository paths without regard to case.
    if (public_repository(origin) or "").lower() != node.repository.lower():
        return "different-repository"
    if dirty:
        return "dirty"
    if head != node.commit:
        return "different-commit"
    return READY


def clone(node: Node, destination: Path, env: dict[str, str]) -> None:
    """Exactly the pinned commit into a new directory."""
    destination.mkdir()
    result = run_git("init", "-q", str(destination), env=env)
    if result.returncode != 0:
        raise LatentCrateError(f"git init failed in {destination}: {(result.stderr or '').strip()}")
    node_git(destination, "remote", "add", "origin", node.repository, env=env)
    node_git(destination, "fetch", "-q", "--depth=1", "origin", node.commit, env=env)
    node_git(destination, "checkout", "-q", "--detach", "FETCH_HEAD", env=env)
    head = node_git(destination, "rev-parse", "HEAD", env=env).lower()
    if head != node.commit:
        raise LatentCrateError(f"node {node.name} resolved to {head}, expected {node.commit}")


# --- Actions -----------------------------------------------------------------


def status(settings: Settings, node_sets: list[NodeSet], env: dict[str, str]) -> int:
    """Print one line per node of the sets; 1 when any node is not ready."""
    return print_states(settings, merged_nodes(node_sets, settings.managed_nodes_dir / RECORD_NAME, ()), env)


def print_states(settings: Settings, node_list: list[Node], env: dict[str, str]) -> int:
    """One line per node with its `node_state`; 1 when any node is not ready."""
    result = 0
    for node in node_list:
        state = node_state(settings.managed_nodes_dir / node.name, node, env)
        print(f"{node.name}: {state} ({node.commit})")
        if state != READY:
            result = 1
    return result


def check(node_sets: list[NodeSet]) -> None:
    """The sets as read: reaching here means the manifests passed the allowlist."""
    for node_set in node_sets:
        print(f"{node_set.name} - {node_set.description}")
        for node in node_set.nodes:
            print(f"{node.name} {node.commit} {node.repository}")


def install(settings: Settings, engine: Engine, selected: list[NodeSet], *, replace_clean: bool) -> None:
    """Add the selected sets to the record, then clone every missing node
    of them at its pinned commit, each staged beside its destination and
    renamed into place; an interruption leaves the sets recorded, so `up`
    refuses until a second install completes them. With `replace_clean`
    (sync), a clean checkout at another commit is replaced the same way;
    every other state refuses. ComfyUI must be stopped, since the image is
    built from these sources."""
    managed = settings.managed_nodes_dir
    env = engine.env
    if shutil.which("git", path=env.get("PATH")) is None:
        raise LatentCrateError("git is required to install a custom-node set; install git")
    with engine.lock("nodes sync" if replace_clean else "nodes install"):
        if engine.container_ids(SERVICE):
            raise LatentCrateError(
                "stop ComfyUI before installing or syncing a custom-node set: bash bin/latentcrate down"
            )
        if managed.is_symlink():
            raise LatentCrateError(f"custom-node directory must not be a symbolic link: {managed}")
        if not managed.is_dir():
            raise LatentCrateError(f"custom-node directory does not exist: {managed}; run: bash bin/latentcrate init")
        wanted = selected_nodes(settings, selected)
        names = dict.fromkeys([*recorded_sets(managed), *(node_set.name for node_set in selected)])
        write_atomic(managed / RECORD_NAME, "".join(f"{name}\n" for name in names))
        for leftover in managed.glob(f"*{STAGING_SUFFIX}"):
            if leftover.is_dir() and not leftover.is_symlink():
                shutil.rmtree(leftover)
        for node in wanted:
            destination = managed / node.name
            state = node_state(destination, node, env)
            if state == READY:
                continue
            if state != "missing" and not (state == "different-commit" and replace_clean):
                raise LatentCrateError(
                    f"refusing to replace {node.name}: status is {state}; move or repair the existing directory first"
                )
            staging = managed / f"{node.name}{STAGING_SUFFIX}"
            try:
                clone(node, staging, env)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            if state == "different-commit":
                replaced = managed / f"{node.name}.replaced{STAGING_SUFFIX}"
                os.replace(destination, replaced)
                os.replace(staging, destination)
                shutil.rmtree(replaced)
            else:
                os.replace(staging, destination)
            print(f"Installed {node.name} at {node.commit}")
        if print_states(settings, wanted, env):
            raise LatentCrateError("the custom-node sets did not reach the requested state; see the lines above")
    print('Run "bash bin/latentcrate up" to build the node dependencies into the image.')


def export(directory: Path, environ: dict[str, str], allowed_hosts: set[str]) -> str:
    """A manifest for the clean public checkouts directly below
    `directory`, in name order; every skipped node is reported on stderr."""
    if directory.is_symlink() or not directory.is_dir():
        raise LatentCrateError(f"custom-node directory does not exist: {directory}")
    env = git_environment(environ)
    entries: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        name = path.name
        if name.startswith(".") or name == "__pycache__" or name.endswith(".disabled"):
            continue
        if not SET_NAME_RE.fullmatch(name):
            reason = "directory name is not valid in a LatentCrate node set"
        elif name.casefold() in seen:
            reason = "another node has the same name when case is ignored"
        elif path.is_symlink():
            reason = "top-level symbolic links cannot be exported"
        elif not is_checkout(path):
            reason = "not a regular Git checkout; copy it through COMFY_LOCAL_NODES_DIR instead"
        elif unsafe_local_config(path, env):
            reason = "checkout has execution-capable or credential-related local Git configuration"
        elif (origin := checkout_git(path, "remote", "get-url", "origin", env=env)).returncode:
            reason = "Git origin is missing or unreadable"
        elif (repository := allowed_repository(https_origin(origin.stdout.strip()), allowed_hosts)) is None:
            reason = "origin is not a public, credential-free repository on an allowed host"
        elif (head := checkout_git(path, "rev-parse", "--verify", "HEAD", env=env)).returncode:
            reason = "HEAD commit is unreadable"
        elif not COMMIT_RE.fullmatch(commit := head.stdout.strip().lower()):
            reason = "HEAD is not a full 40-character commit"
        elif (changes := checkout_git(path, *DIRTY_QUERY, "--ignore-submodules=all", env=env)).returncode:
            reason = "working-tree state could not be inspected safely"
        elif changes.stdout.strip():
            reason = f"working tree is dirty at {commit}; uncommitted changes cannot be pinned"
        else:
            reason = ""
            entries.append(f'[[node]]\nname = "{name}"\nrepository = "{repository}"\ncommit = "{commit}"\n')
        seen.add(name.casefold())
        if reason:
            skipped.append(name)
            warn(f"Needs manual handling: {name} - {reason}")
    if not entries:
        raise LatentCrateError(f"no clean public node checkouts could be exported from {directory}")
    warn(
        f"Exported {len(entries)} node(s); {len(skipped)} need manual handling. "
        "Review every commit before sharing or installing the set."
    )
    return f'schema = 1\ndescription = "{EXPORT_DESCRIPTION}"\n\n' + "\n".join(entries)


def https_origin(origin: str) -> str:
    """An SSH origin (`git@host:owner/repo`, `ssh://git@host/owner/repo`)
    as its HTTPS spelling; any other origin as written."""
    match = SSH_ORIGIN_RE.fullmatch(origin)
    if match is None:
        return origin
    return f"https://{match.group(1) or match.group(2)}/{match.group(3)}"
