#!/usr/bin/env python3
"""Install or inspect a commit-pinned set of public ComfyUI third-party nodes."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
REPOSITORY_PATH_RE = re.compile(r"^/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+(?:\.git)?$")


def fail(message: str) -> NoReturn:
    raise SystemExit(f"LatentCrate: {message}")


def remove_tree(path: Path) -> None:
    def make_writable(function, failing_path, _error):
        os.chmod(failing_path, stat.S_IWRITE)
        function(failing_path)

    shutil.rmtree(path, onexc=make_writable)


@dataclass(frozen=True)
class Node:
    name: str
    repository: str
    commit: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "status", "sync"))
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--allowed-git-hosts", required=True, type=Path)
    return parser.parse_args()


def load_allowed_hosts(path: Path) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        fail(f"could not read allowed Git hosts: {error}")
    hosts = {
        line.strip().lower()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    }
    if not hosts or any(not HOST_RE.fullmatch(host) for host in hosts):
        fail("allowed Git hosts must contain valid lowercase host names")
    return hosts


def normalize_public_repository(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or not REPOSITORY_PATH_RE.fullmatch(parsed.path)
    ):
        return None
    path = parsed.path[:-4] if parsed.path.lower().endswith(".git") else parsed.path
    return f"https://{parsed.hostname.lower()}{path}".lower()


def validate_repository(value: object, allowed_hosts: set[str]) -> str:
    normalized = normalize_public_repository(value)
    if normalized is None or urlsplit(normalized).hostname not in allowed_hosts:
        fail(
            "node repositories must be credential-free HTTPS owner/repository URLs "
            "on a host listed in allowed-git-hosts.txt"
        )
    return normalized


def load_manifest(path: Path, allowed_hosts: set[str]) -> tuple[str, list[Node]]:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        fail(f"could not read node-set manifest: {error}")
    if document.get("version") != 1:
        fail("node-set manifest version must be 1")
    description = document.get("description", "")
    if not isinstance(description, str):
        fail("node-set description must be a string")
    raw_nodes = document.get("node")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        fail("node-set manifest must contain at least one [[node]] entry")

    nodes: list[Node] = []
    names: set[str] = set()
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict) or set(raw_node) != {"name", "repository", "commit"}:
            fail("each [[node]] must contain only name, repository, and commit")
        name = raw_node["name"]
        commit = raw_node["commit"]
        if not isinstance(name, str) or not NAME_RE.fullmatch(name) or name.startswith("."):
            fail(f"unsafe custom-node directory name: {name!r}")
        if name.casefold() in names:
            fail(f"duplicate custom-node name in set: {name}")
        names.add(name.casefold())
        if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
            fail(f"node {name} must use a full 40-character Git commit")
        nodes.append(
            Node(
                name=name,
                repository=validate_repository(raw_node["repository"], allowed_hosts),
                commit=commit.lower(),
            )
        )
    return description, nodes


def git_environment(home: Path) -> dict[str, str]:
    # This Python copy of the Git config/env hardening options mirrors the
    # canonical Bash copy in scripts/resolve-frontend.sh (hardened_git); keep
    # the two aligned when either changes.
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_ASKPASS": "/bin/false",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_COUNT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "HOME": str(home),
        }
    )
    return environment


def git(path: Path, *arguments: str, capture: bool = False) -> str:
    command = [
        "git",
        "-c",
        "credential.helper=",
        "-c",
        "core.askPass=/bin/false",
        "-c",
        "http.followRedirects=false",
    ]
    if arguments and arguments[0] == "init":
        command.extend(("-C", str(path)))
    else:
        command.extend(
            (
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.untrackedCache=false",
                f"--git-dir={path / '.git'}",
                f"--work-tree={path}",
            )
        )
    command.extend(arguments)
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        env=git_environment(Path("/tmp/latentcrate-node-set-home")),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() if capture else "git command failed"
        fail(f"{detail}: {' '.join(arguments)}")
    return result.stdout.strip() if capture else ""


def existing_status(path: Path, node: Node) -> str:
    if path.is_symlink():
        return "conflict"
    if not os.path.lexists(path):
        return "missing"
    if (
        not path.is_dir()
        or path.is_symlink()
        or not (path / ".git").is_dir()
        or (path / ".git").is_symlink()
    ):
        return "conflict"
    assert_safe_local_git_config(path)
    origin = git(path, "remote", "get-url", "origin", capture=True)
    head = git(path, "rev-parse", "HEAD", capture=True).lower()
    dirty = bool(git(path, "status", "--porcelain", "--untracked-files=all", capture=True))
    if normalize_public_repository(origin) != node.repository:
        return "different-repository"
    if dirty:
        return "dirty"
    if head != node.commit:
        return "different-commit"
    return "ready"


def assert_safe_local_git_config(path: Path) -> None:
    command = [
        "git",
        f"--git-dir={path / '.git'}",
        f"--work-tree={path}",
        "config",
        "--local",
        "--no-includes",
        "--name-only",
        "--get-regexp",
        r"^(filter\.|diff\..*\.(command|textconv)$|core\.(fsmonitor|hookspath|attributesfile|sshcommand)$|include\.|includeif\.|url\.|credential\.|http\.)",
    ]
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=git_environment(Path("/tmp/latentcrate-node-set-home")),
    )
    if result.returncode not in (0, 1):
        fail(f"could not inspect local Git configuration for {path}: {result.stderr.strip()}")
    if result.returncode == 0:
        fail(f"refusing execution-capable local Git configuration in {path}")


def clone_node(node: Node, destination: Path) -> None:
    destination.mkdir()
    git(destination, "init")
    git(destination, "remote", "add", "origin", node.repository)
    git(destination, "fetch", "--depth=1", "origin", node.commit)
    git(destination, "checkout", "--detach", "FETCH_HEAD")
    head = git(destination, "rev-parse", "HEAD", capture=True).lower()
    if head != node.commit:
        fail(f"node {node.name} resolved to {head}, expected {node.commit}")


def report_status(target: Path, nodes: list[Node]) -> bool:
    pending = sorted(target.glob(".latentcrate-node-set-*.disabled"))
    if pending:
        print(
            "pending node-set transaction: run nodes install or nodes sync to recover "
            f"({pending[0].name})"
        )
        return False
    all_ready = True
    for node in nodes:
        status = existing_status(target / node.name, node)
        print(f"{node.name}: {status} ({node.commit})")
        all_ready = all_ready and status == "ready"
    return all_ready


def transaction_destination_is_expected(destination: Path, entry: dict[str, str]) -> bool:
    node = Node(entry["name"], entry["repository"], entry["commit"])
    return existing_status(destination, node) == "ready"


@contextlib.contextmanager
def node_set_lock(target: Path):
    lock_path = target / ".latentcrate-node-set.lock"
    if not lock_path.exists():
        try:
            with lock_path.open("xb") as new_lock:
                new_lock.write(b"\0")
        except FileExistsError:
            pass
        except OSError as error:
            fail(f"could not create custom-node set lock {lock_path}: {error}")
    try:
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            fail(f"custom-node set lock is not a regular file: {lock_path}")
        with os.fdopen(descriptor, "r+b") as lock_file:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except OSError as error:
        fail(f"could not lock custom-node directory {target}: {error}")


def recover_interrupted_install(target: Path) -> None:
    for staging in sorted(target.glob(".latentcrate-node-set-*.disabled")):
        if staging.is_symlink() or not staging.is_dir():
            fail(f"node-set staging path is not a regular directory: {staging}")
        transaction = staging / "transaction.json"
        if not transaction.is_file():
            remove_tree(staging)
            continue
        try:
            entries = json.loads(transaction.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            fail(f"could not recover interrupted node-set transaction {staging}: {error}")
        if (
            not isinstance(entries, list)
            or any(
                not isinstance(entry, dict)
                or set(entry) != {"name", "status", "repository", "commit"}
                or not isinstance(entry["name"], str)
                or not NAME_RE.fullmatch(entry["name"])
                or entry["status"] not in {"missing", "different-commit"}
                or not isinstance(entry["repository"], str)
                or normalize_public_repository(entry["repository"]) != entry["repository"]
                or not isinstance(entry["commit"], str)
                or not COMMIT_RE.fullmatch(entry["commit"])
                for entry in entries
            )
        ):
            fail(f"invalid interrupted node-set transaction: {staging}")
        committed = (staging / "committed").is_file()
        for entry in entries:
            name = entry["name"]
            backup = staging / f".backup-{name}"
            destination = target / name
            if backup.is_symlink():
                fail(f"node-set transaction backup must not be a symlink: {backup}")
            if committed:
                if backup.exists():
                    remove_tree(backup)
                continue
            if entry["status"] == "missing":
                if os.path.lexists(destination):
                    if not transaction_destination_is_expected(destination, entry):
                        fail(
                            f"cannot safely roll back changed interrupted node install: "
                            f"{destination}; preserve it and recover manually"
                        )
                    remove_tree(destination)
            elif backup.exists():
                if os.path.lexists(destination):
                    if not transaction_destination_is_expected(destination, entry):
                        fail(
                            f"cannot safely roll back changed interrupted node sync: "
                            f"{destination}; preserve it and recover manually"
                        )
                    remove_tree(destination)
                backup.replace(destination)
        remove_tree(staging)


def install(target: Path, nodes: list[Node], *, replace_clean: bool) -> None:
    if target.is_symlink():
        fail(f"custom-node target must not be a symlink: {target}")
    target.mkdir(parents=True, exist_ok=True)
    recover_interrupted_install(target)

    planned: list[tuple[Node, str]] = []
    for node in nodes:
        status = existing_status(target / node.name, node)
        if status == "missing":
            planned.append((node, status))
        elif status == "different-commit" and replace_clean:
            planned.append((node, status))
        elif status != "ready":
            fail(
                f"refusing to replace {node.name}: status is {status}; move or repair "
                "the existing directory first"
            )

    if not planned:
        print("All nodes in the set are already installed at the requested commits.")
        return

    staging = Path(
        tempfile.mkdtemp(prefix=".latentcrate-node-set-", suffix=".disabled", dir=target)
    )
    transaction_active = False
    try:
        for node, _ in planned:
            clone_node(node, staging / node.name)
        for node, original_status in planned:
            destination = target / node.name
            current_status = existing_status(destination, node)
            if current_status != original_status:
                fail(f"custom-node destination appeared during installation: {destination}")
        (staging / "transaction.json").write_text(
            json.dumps(
                [
                    {
                        "name": node.name,
                        "status": status,
                        "repository": node.repository,
                        "commit": node.commit,
                    }
                    for node, status in planned
                ]
            ),
            encoding="utf-8",
        )
        transaction_active = True
        promoted: list[Node] = []
        replaced: list[Node] = []
        try:
            for node, original_status in planned:
                if original_status == "different-commit":
                    (target / node.name).replace(staging / f".backup-{node.name}")
                    replaced.append(node)
                (staging / node.name).replace(target / node.name)
                promoted.append(node)
        except BaseException:
            try:
                for node in reversed(promoted):
                    destination = target / node.name
                    if os.path.lexists(destination) and not (staging / node.name).exists():
                        destination.replace(staging / node.name)
                for node in reversed(replaced):
                    backup = staging / f".backup-{node.name}"
                    destination = target / node.name
                    if backup.exists() and not os.path.lexists(destination):
                        backup.replace(destination)
            except BaseException:
                # Preserve the transaction record, staged trees, and backups so
                # the next invocation can recover them instead of losing state.
                raise
            transaction_active = False
            raise
        for node in promoted:
            print(f"Installed {node.name} at {node.commit}")
        (staging / "committed").touch()
        transaction_active = False
    finally:
        if not transaction_active:
            remove_tree(staging)


def main() -> None:
    args = parse_args()
    allowed_hosts = load_allowed_hosts(args.allowed_git_hosts)
    description, nodes = load_manifest(args.manifest, allowed_hosts)
    if description:
        print(description)
    args.target.mkdir(parents=True, exist_ok=True)
    if args.action == "status":
        if not report_status(args.target, nodes):
            raise SystemExit(1)
    else:
        with node_set_lock(args.target):
            install(args.target, nodes, replace_clean=args.action == "sync")
            if not report_status(args.target, nodes):
                fail("node-set installation did not produce the requested state")


if __name__ == "__main__":
    main()
