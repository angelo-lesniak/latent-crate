#!/usr/bin/env python3
"""Create a reviewed, self-contained third-party node dependency snapshot."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn
from urllib.parse import urlsplit

try:
    from packaging.requirements import InvalidRequirement, Requirement
except ModuleNotFoundError:  # pip vendors packaging in supported helper/test runtimes
    from pip._vendor.packaging.requirements import InvalidRequirement, Requirement


INCLUDE_RE = re.compile(
    r"^\s*(?:-r|--requirement|-c|--constraint)(?:\s+|=)(?P<path>[^\s#]+)"
    r"(?:\s+#.*)?\s*$"
)
EMBEDDED_CREDENTIAL_RE = re.compile(
    r"[a-z][a-z0-9+.-]*://[^/\s@]+@", re.IGNORECASE
)
TOKEN_RE = re.compile(
    r"(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}|pypi-[A-Za-z0-9_-]{20,})"
)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
REPOSITORY_PATH_RE = re.compile(r"^/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+(?:\.git)?$")
VCS_PREFIX_RE = re.compile(r"(?:git|hg|svn|bzr)\+", re.IGNORECASE)


def fail(message: str) -> NoReturn:
    raise SystemExit(f"LatentCrate: {message}")


@dataclass(frozen=True)
class VcsPin:
    package: str
    repository: str
    normalized_repository: str
    requested_ref: str
    commit: str


@dataclass(frozen=True)
class PackageReplacement:
    source: str
    target: str
    drop_extras: frozenset[str]
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, action="append", type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--include-file", required=True, type=Path)
    parser.add_argument("--vcs-pins", required=True, type=Path)
    parser.add_argument("--package-replacements", required=True, type=Path)
    parser.add_argument("--allowed-git-hosts", required=True, type=Path)
    return parser.parse_args()


def read_text(path: Path, description: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        fail(f"could not read {description} {path}: {error}")


def load_allowed_hosts(text: str) -> set[str]:
    hosts = {
        line.strip().lower()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if not hosts:
        fail("allowed Git hosts file must contain at least one host")
    for host in hosts:
        if host != host.lower() or not HOST_RE.fullmatch(host):
            fail(f"invalid allowed Git host: {host}")
    return hosts


def normalize_repository(value: str, allowed_hosts: set[str]) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.lower() not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or not REPOSITORY_PATH_RE.fullmatch(parsed.path)
    ):
        fail(
            "Git repositories must be credential-free HTTPS owner/repository URLs "
            "on a host listed in allowed-git-hosts.txt"
        )
    path = parsed.path[:-4] if parsed.path.endswith(".git") else parsed.path
    return f"https://{parsed.hostname.lower()}{path}".lower()


def load_vcs_pins(text: str, allowed_hosts: set[str]) -> list[VcsPin]:
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        fail(f"invalid VCS pin file: {error}")
    if document.get("version") != 1:
        fail("VCS pin file version must be 1")
    raw_pins = document.get("pin", [])
    if not isinstance(raw_pins, list):
        fail("VCS pin file must use [[pin]] entries")

    pins: list[VcsPin] = []
    repositories: set[str] = set()
    for raw_pin in raw_pins:
        if not isinstance(raw_pin, dict) or set(raw_pin) != {
            "package",
            "repository",
            "requested_ref",
            "commit",
        }:
            fail(
                "each [[pin]] must contain only package, repository, "
                "requested_ref, and commit"
            )
        package = raw_pin["package"]
        repository = raw_pin["repository"]
        requested_ref = raw_pin["requested_ref"]
        commit = raw_pin["commit"]
        if not isinstance(package, str) or not NAME_RE.fullmatch(package):
            fail(f"invalid package name in VCS pin: {package!r}")
        if not isinstance(repository, str):
            fail("VCS pin repository must be a string")
        if (
            not isinstance(requested_ref, str)
            or requested_ref.startswith("-")
            or any(character.isspace() for character in requested_ref)
            or any(character in requested_ref for character in "?#@")
        ):
            fail(f"invalid requested_ref in VCS pin for {package}")
        if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
            fail(f"VCS pin for {package} must use a full 40-character Git commit")
        normalized = normalize_repository(repository, allowed_hosts)
        if normalized in repositories:
            fail(f"duplicate VCS pin repository: {repository}")
        repositories.add(normalized)
        pins.append(VcsPin(package, repository, normalized, requested_ref, commit.lower()))
    return pins


def load_package_replacements(text: str) -> dict[str, PackageReplacement]:
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        fail(f"invalid package replacement file: {error}")
    if document.get("version") != 1:
        fail("package replacement file version must be 1")
    raw_replacements = document.get("replacement", [])
    if not isinstance(raw_replacements, list):
        fail("package replacement file must use [[replacement]] entries")

    replacements: dict[str, PackageReplacement] = {}
    for raw_replacement in raw_replacements:
        if not isinstance(raw_replacement, dict) or set(raw_replacement) != {
            "from",
            "to",
            "drop_extras",
            "reason",
        }:
            fail(
                "each [[replacement]] must contain only from, to, "
                "drop_extras, and reason"
            )
        source = raw_replacement["from"]
        target = raw_replacement["to"]
        raw_drop_extras = raw_replacement["drop_extras"]
        reason = raw_replacement["reason"]
        if not isinstance(source, str) or not NAME_RE.fullmatch(source):
            fail(f"invalid source name in package replacement: {source!r}")
        if not isinstance(target, str) or not NAME_RE.fullmatch(target):
            fail(f"invalid target name in package replacement: {target!r}")
        if (
            not isinstance(raw_drop_extras, list)
            or any(
                not isinstance(extra, str) or not NAME_RE.fullmatch(extra)
                for extra in raw_drop_extras
            )
        ):
            fail(f"drop_extras must list safe extra names for package replacement {source}")
        drop_extras = frozenset(extra.lower() for extra in raw_drop_extras)
        if len(drop_extras) != len(raw_drop_extras):
            fail(f"package replacement {source} contains duplicate extras")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or reason != reason.strip()
            or any(character in reason for character in "\r\n\0")
        ):
            fail(f"package replacement {source} must have a short single-line reason")
        canonical_source = package_base(source)
        canonical_target = package_base(target)
        if canonical_source == canonical_target:
            fail(f"package replacement must change the package name: {source}")
        if canonical_source in replacements:
            fail(f"duplicate package replacement source: {source}")
        replacements[canonical_source] = PackageReplacement(
            source, target, drop_extras, reason
        )

    replacement_sources = set(replacements)
    for replacement in replacements.values():
        if package_base(replacement.target) in replacement_sources:
            fail(
                f"package replacement chains are not supported: "
                f"{replacement.source} -> {replacement.target}"
            )
    return replacements


def relative_to_node(path: Path, node_root: Path) -> Path:
    try:
        return path.resolve(strict=True).relative_to(node_root.resolve(strict=True))
    except (FileNotFoundError, ValueError):
        fail(f"requirement include escapes its custom-node root: {path}")


def scan_for_secrets(path: Path, text: str) -> None:
    if EMBEDDED_CREDENTIAL_RE.search(text) or TOKEN_RE.search(text):
        fail(f"possible credential found in requirement file: {path}")


def validate_manifest_path(path: Path) -> None:
    has_control_character = any(character in str(path) for character in "\r\n\0")
    if path.is_absolute() or ".." in path.parts or has_control_character:
        fail(f"unsafe requirement manifest path: {path}")


def package_base(value: str) -> str:
    return value.split("[", 1)[0].strip().lower().replace("_", "-").replace(".", "-")


def rewrite_vcs_line(
    path: Path,
    line: str,
    pins: list[VcsPin],
    allowed_hosts: set[str],
) -> tuple[str, dict[str, str] | None]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return line, None
    inspected = re.split(r"\s+#", stripped, maxsplit=1)[0]
    if not VCS_PREFIX_RE.search(inspected):
        return line, None
    if line.rstrip().endswith("\\"):
        fail(f"multiline VCS requirements are not supported: {path}")

    indent = line[: len(line) - len(line.lstrip())]
    body = stripped
    comment_suffix = ""
    comment_match = re.search(r"\s+#", body)
    if comment_match:
        comment_suffix = body[comment_match.start() :]
        body = body[: comment_match.start()]
    editable = False
    if body.startswith("-e ") or body.startswith("--editable "):
        editable = True
        body = body.split(maxsplit=1)[1]

    marker_parts = re.split(r"\s*;\s*", body, maxsplit=1)
    requirement_body = marker_parts[0]
    marker_suffix = f" ; {marker_parts[1]}" if len(marker_parts) == 2 else ""
    body = requirement_body

    package: str | None = None
    if " @ " in body:
        package, vcs_url = body.split(" @ ", 1)
        package = package.strip()
    else:
        vcs_url = body

    if not vcs_url.startswith("git+https://"):
        fail(f"only git+https VCS requirements are supported: {path}: {line.strip()}")
    raw_url = vcs_url[len("git+") :]
    parsed = urlsplit(raw_url)
    if parsed.query:
        fail(f"Git requirement URLs must not contain a query: {path}: {line.strip()}")
    if parsed.fragment and not re.fullmatch(
        r"(?:subdirectory=[A-Za-z0-9._/-]+|egg=[A-Za-z0-9._-]+)", parsed.fragment
    ):
        fail(f"unsupported Git requirement fragment: {path}: {parsed.fragment}")
    if parsed.fragment.startswith("subdirectory="):
        subdirectory = PurePosixPath(parsed.fragment.removeprefix("subdirectory="))
        if subdirectory.is_absolute() or ".." in subdirectory.parts:
            fail(f"unsafe Git requirement fragment: {path}: {parsed.fragment}")
    repository_path, separator, revision = parsed.path.rpartition("@")
    if not separator:
        repository_path = parsed.path
        revision = ""
    repository = f"https://{parsed.netloc}{repository_path}"
    normalized = normalize_repository(repository, allowed_hosts)
    matching_pin = next((pin for pin in pins if pin.normalized_repository == normalized), None)

    if COMMIT_RE.fullmatch(revision):
        return line, None
    if matching_pin is None:
        fail(
            f"unpinned Git requirement in {path}: {line.strip()}; add a reviewed "
            "entry to config/custom-nodes/vcs-pins.toml"
        )
    if revision != matching_pin.requested_ref:
        shown = revision or "<no revision>"
        expected = matching_pin.requested_ref or "<no revision>"
        fail(
            f"Git requirement in {path} requests {shown}, but the reviewed VCS "
            f"override permits only {expected} for {matching_pin.repository}"
        )
    if package is not None and package_base(package) != package_base(matching_pin.package):
        fail(
            f"VCS pin package {matching_pin.package} does not match requirement package "
            f"{package} in {path}"
        )

    effective_package = package or matching_pin.package
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    effective = (
        f"{indent}{'-e ' if editable else ''}{effective_package} @ "
        f"git+{matching_pin.repository}@{matching_pin.commit}{fragment}"
        f"{marker_suffix}{comment_suffix}"
    )
    return effective, {
        "file": str(path),
        "original": line.strip(),
        "effective": effective.strip(),
    }


def rewrite_package_line(
    path: Path,
    line: str,
    replacements: dict[str, PackageReplacement],
) -> tuple[str, dict[str, str] | None]:
    stripped = line.strip()
    if (
        not stripped
        or stripped.startswith("#")
        or INCLUDE_RE.match(stripped)
        or VCS_PREFIX_RE.search(stripped)
        or stripped.startswith("-")
    ):
        return line, None
    if line.rstrip().endswith("\\"):
        fail(f"multiline package requirements are not supported: {path}")

    indent = line[: len(line) - len(line.lstrip())]
    body = stripped
    comment_suffix = ""
    comment_match = re.search(r"\s+#", body)
    if comment_match:
        comment_suffix = body[comment_match.start() :]
        body = body[: comment_match.start()].rstrip()
    try:
        requirement = Requirement(body)
    except InvalidRequirement as error:
        fail(f"invalid custom-node requirement in {path}: {body}: {error}")
    replacement = replacements.get(package_base(requirement.name))
    if replacement is None:
        return line, None
    if requirement.url is not None:
        fail(f"package replacements do not support direct URLs: {path}: {body}")

    retained_extras = {
        extra for extra in requirement.extras if extra.lower() not in replacement.drop_extras
    }
    extras = f"[{','.join(sorted(retained_extras))}]" if retained_extras else ""
    marker = f" ; {requirement.marker}" if requirement.marker is not None else ""
    effective = (
        f"{indent}{replacement.target}{extras}{requirement.specifier}"
        f"{marker}{comment_suffix}"
    )
    return effective, {
        "file": str(path),
        "original": line.strip(),
        "effective": effective.strip(),
        "reason": replacement.reason,
    }


def validate_requirement_line(path: Path, line: str) -> None:
    body = line.strip()
    if not body or body.startswith("#"):
        return
    comment_match = re.search(r"\s+#", body)
    if comment_match:
        body = body[: comment_match.start()].rstrip()
    if INCLUDE_RE.match(body):
        return
    if VCS_PREFIX_RE.search(body):
        return
    if body.startswith("-"):
        fail(
            f"pip source and resolver options are not allowed in custom-node "
            f"requirements: {path}: {body}"
        )
    try:
        requirement = Requirement(body)
    except InvalidRequirement as error:
        fail(f"invalid custom-node requirement in {path}: {body}: {error}")
    if requirement.url is not None:
        fail(
            f"direct URL requirements must use a reviewed full-commit git+https "
            f"source: {path}: {body}"
        )


def rewrite_requirements(
    path: Path,
    text: str,
    pins: list[VcsPin],
    replacements: dict[str, PackageReplacement],
    allowed_hosts: set[str],
) -> tuple[str, list[dict[str, str]], list[dict[str, str]]]:
    output: list[str] = []
    vcs_rewrites: list[dict[str, str]] = []
    package_rewrites: list[dict[str, str]] = []
    for line in text.splitlines(keepends=True):
        if line.endswith("\r\n"):
            content, ending = line[:-2], "\n"
        elif line.endswith(("\n", "\r")):
            content, ending = line[:-1], "\n"
        else:
            content, ending = line, ""
        validate_requirement_line(path, content)
        rewritten, vcs_record = rewrite_vcs_line(path, content, pins, allowed_hosts)
        rewritten, package_record = rewrite_package_line(path, rewritten, replacements)
        output.append(rewritten + ending)
        if vcs_record:
            vcs_rewrites.append(vcs_record)
        if package_record:
            package_rewrites.append(package_record)
    return "".join(output), vcs_rewrites, package_rewrites


def copy_requirement_tree(
    source: Path,
    node_root: Path,
    destination_root: Path,
    destination_prefix: Path,
    copied: set[Path],
    source_contents: dict[Path, bytes],
    vcs_rewrites: list[dict[str, str]],
    package_rewrites: list[dict[str, str]],
    pins: list[VcsPin],
    replacements: dict[str, PackageReplacement],
    allowed_hosts: set[str],
) -> None:
    source = source.resolve(strict=True)
    relative = relative_to_node(source, node_root)
    target_relative = destination_prefix / relative
    if target_relative in copied:
        return

    try:
        raw = source.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as error:
        fail(f"could not read requirement file {source}: {error}")
    scan_for_secrets(source, text)
    effective, file_vcs_rewrites, file_package_rewrites = rewrite_requirements(
        source, text, pins, replacements, allowed_hosts
    )
    target = destination_root / target_relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(effective, encoding="utf-8")
    copied.add(target_relative)
    source_contents[source] = raw
    vcs_rewrites.extend(file_vcs_rewrites)
    package_rewrites.extend(file_package_rewrites)

    for line in text.splitlines():
        match = INCLUDE_RE.match(line)
        if not match:
            continue
        include = (source.parent / match.group("path")).resolve(strict=False)
        relative_to_node(include, node_root)
        if not include.is_file():
            fail(f"referenced requirement file does not exist: {include}")
        copy_requirement_tree(
            include,
            node_root,
            destination_root,
            destination_prefix,
            copied,
            source_contents,
            vcs_rewrites,
            package_rewrites,
            pins,
            replacements,
            allowed_hosts,
        )


def configured_patterns(text: str) -> list[str]:
    patterns: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if Path(line).is_absolute() or ".." in Path(line).parts:
            fail(f"unsafe custom-node requirement include pattern: {line}")
        patterns.append(line)
    return patterns


def discover_entries(
    source_roots: list[Path], patterns: list[str]
) -> list[tuple[Path, Path, Path]]:
    entries: list[tuple[Path, Path, Path]] = []
    nodes: dict[str, Path] = {}
    for source_root in source_roots:
        for node_entry in sorted(source_root.iterdir(), key=lambda item: item.name.casefold()):
            if node_entry.is_symlink():
                fail(
                    f"top-level custom-node symlinks are not allowed: {node_entry}; "
                    "place the checkout under COMFY_LOCAL_NODES_DIR"
                )
            if node_entry.is_dir() and node_entry.name.startswith("."):
                fail(
                    f"hidden custom-node directories are not supported: {node_entry}; "
                    "rename it or suffix it with .disabled"
                )
            if (
                not node_entry.is_dir()
                or node_entry.name.endswith(".disabled")
                or node_entry.name == "__pycache__"
            ):
                continue
            folded = node_entry.name.casefold()
            if folded in nodes:
                fail(
                    f"duplicate custom-node directory name {node_entry.name}: "
                    f"{nodes[folded]} and {node_entry}"
                )
            nodes[folded] = node_entry
            node_root = node_entry.resolve(strict=True)
            default = node_root / "requirements.txt"
            if default.is_file():
                entries.append((default, node_root, Path(node_entry.name)))

    for pattern in patterns:
        candidates: list[tuple[Path, Path]] = []
        for source_root in source_roots:
            candidates.extend(
                (source_root, candidate)
                for candidate in source_root.glob(pattern)
                if candidate.is_file()
                and len(candidate.relative_to(source_root).parts) >= 2
                and not candidate.relative_to(source_root).parts[0].endswith(".disabled")
                and candidate.relative_to(source_root).parts[0] != "__pycache__"
            )
        candidates.sort(key=lambda pair: str(pair[1]).casefold())
        if not candidates:
            fail(f"configured custom-node requirement pattern matched nothing: {pattern}")
        for source_root, candidate in candidates:
            relative_from_source = candidate.relative_to(source_root)
            if len(relative_from_source.parts) < 2:
                fail(f"custom requirement must belong to a node directory: {candidate}")
            node_entry = source_root / relative_from_source.parts[0]
            node_root = node_entry.resolve(strict=True)
            relative_to_node(candidate, node_root)
            entries.append((candidate, node_root, Path(node_entry.name)))
    return entries


def build_snapshot(
    source_roots: list[Path],
    staging_root: Path,
    patterns: list[str],
    pins: list[VcsPin],
    replacements: dict[str, PackageReplacement],
    allowed_hosts: set[str],
    policy_digest: str,
) -> tuple[int, int, int, int]:
    entries = discover_entries(source_roots, patterns)
    original_entrypoints = {entry[0].resolve(strict=True) for entry in entries}
    copied: set[Path] = set()
    manifest_entries: list[Path] = []
    seen_sources: set[Path] = set()
    source_contents: dict[Path, bytes] = {}
    vcs_rewrites: list[dict[str, str]] = []
    package_rewrites: list[dict[str, str]] = []
    for requirement, node_root, prefix in entries:
        resolved = requirement.resolve(strict=True)
        if resolved in seen_sources:
            continue
        seen_sources.add(resolved)
        relative = relative_to_node(resolved, node_root)
        manifest_entry = prefix / relative
        validate_manifest_path(manifest_entry)
        manifest_entries.append(manifest_entry)
        copy_requirement_tree(
            resolved,
            node_root,
            staging_root,
            prefix,
            copied,
            source_contents,
            vcs_rewrites,
            package_rewrites,
            pins,
            replacements,
            allowed_hosts,
        )

    current_entrypoints = {
        entry[0].resolve(strict=True) for entry in discover_entries(source_roots, patterns)
    }
    if current_entrypoints != original_entrypoints:
        fail("custom-node requirement files changed while dependencies were being captured")
    for source, original in source_contents.items():
        try:
            current = source.read_bytes()
        except OSError as error:
            fail(f"requirement file changed while dependencies were being captured: {source}: {error}")
        if current != original:
            fail(f"requirement file changed while dependencies were being captured: {source}")

    (staging_root / "manifest.txt").write_text(
        "".join(
            f"{path.as_posix()}\n"
            for path in sorted(manifest_entries, key=lambda value: value.as_posix().casefold())
        ),
        encoding="utf-8",
    )
    (staging_root / "vcs-rewrites.jsonl").write_text(
        "".join(
            json.dumps(record, sort_keys=True) + "\n"
            for record in sorted(
                vcs_rewrites, key=lambda value: (value["file"], value["original"])
            )
        ),
        encoding="utf-8",
    )
    (staging_root / "package-rewrites.jsonl").write_text(
        "".join(
            json.dumps(record, sort_keys=True) + "\n"
            for record in sorted(
                package_rewrites, key=lambda value: (value["file"], value["original"])
            )
        ),
        encoding="utf-8",
    )
    (staging_root / "policy.sha256").write_text(f"{policy_digest}\n", encoding="utf-8")
    return (
        len(manifest_entries),
        len(copied),
        len(vcs_rewrites),
        len(package_rewrites),
    )


def replace_path(source: Path, destination: Path) -> None:
    """Keep atomic promotion reliable when Windows briefly locks a directory.

    The production helper runs on Linux. Contributor tests also run directly on
    Windows, where antivirus and filesystem indexing can hold a just-created
    directory for a fraction of a second.
    """
    attempts = 40 if os.name == "nt" else 1
    for attempt in range(attempts):
        try:
            source.replace(destination)
            return
        except PermissionError:
            if attempt + 1 == attempts:
                raise
            time.sleep(0.05)


def recover_snapshot(destination_root: Path, backup_root: Path) -> None:
    if backup_root.exists():
        if destination_root.exists():
            shutil.rmtree(backup_root)
        else:
            replace_path(backup_root, destination_root)


def promote_snapshot(staging_root: Path, destination_root: Path, backup_root: Path) -> None:
    had_destination = destination_root.exists()
    if had_destination:
        replace_path(destination_root, backup_root)

    try:
        replace_path(staging_root, destination_root)
    except BaseException:
        if had_destination and backup_root.exists() and not destination_root.exists():
            replace_path(backup_root, destination_root)
        raise
    else:
        if had_destination:
            shutil.rmtree(backup_root)


@contextlib.contextmanager
def exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

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


def main() -> None:
    args = parse_args()
    source_roots = [source.resolve() for source in args.source]
    destination_input = args.destination.absolute()
    if destination_input.is_symlink():
        fail(f"snapshot destination must not be a symlink: {destination_input}")
    destination_root = destination_input.resolve()
    backup_root = destination_root.with_name(f".{destination_root.name}.backup")
    lock_path = destination_root.with_name(f".{destination_root.name}.lock")

    if destination_root.name != "custom-node-requirements":
        fail(f"refusing unexpected snapshot destination: {destination_root}")
    if len(set(source_roots)) != len(source_roots):
        fail("custom-node source directories must be distinct")
    for source_root in source_roots:
        if not source_root.is_dir():
            fail(f"custom-node source directory does not exist: {source_root}")

    with exclusive_lock(lock_path):
        policy_paths = (
            args.include_file,
            args.vcs_pins,
            args.package_replacements,
            args.allowed_git_hosts,
        )
        try:
            policy_contents = {path: path.read_bytes() for path in policy_paths}
            policy_text = {
                path: content.decode("utf-8") for path, content in policy_contents.items()
            }
        except (OSError, UnicodeError) as error:
            fail(f"could not read custom-node capture policy: {error}")
        patterns = configured_patterns(policy_text[args.include_file])
        allowed_hosts = load_allowed_hosts(policy_text[args.allowed_git_hosts])
        pins = load_vcs_pins(policy_text[args.vcs_pins], allowed_hosts)
        replacements = load_package_replacements(
            policy_text[args.package_replacements]
        )
        policy_digest = hashlib.sha256(
            b"\0".join(policy_contents[path] for path in policy_paths)
        ).hexdigest()
        if destination_root.exists() and (
            not destination_root.is_dir() or destination_root.is_symlink()
        ):
            fail(f"snapshot destination is not a regular directory: {destination_root}")
        if backup_root.exists() and (
            not backup_root.is_dir() or backup_root.is_symlink()
        ):
            fail(f"snapshot backup is not a regular directory: {backup_root}")
        recover_snapshot(destination_root, backup_root)
        for stale_staging in destination_root.parent.glob(
            f".{destination_root.name}.staging-*"
        ):
            if stale_staging.is_symlink() or not stale_staging.is_dir():
                fail(f"snapshot staging path is not a regular directory: {stale_staging}")
            shutil.rmtree(stale_staging)
        staging_root = Path(
            tempfile.mkdtemp(
                prefix=f".{destination_root.name}.staging-", dir=destination_root.parent
            )
        )
        try:
            (staging_root / ".gitkeep").touch()
            (
                manifest_count,
                copied_count,
                vcs_rewrite_count,
                package_rewrite_count,
            ) = build_snapshot(
                source_roots,
                staging_root,
                patterns,
                pins,
                replacements,
                allowed_hosts,
                policy_digest,
            )
            if any(path.read_bytes() != policy_contents[path] for path in policy_paths):
                fail("custom-node capture policy changed while dependencies were being captured")
            promote_snapshot(staging_root, destination_root, backup_root)
        except BaseException:
            shutil.rmtree(staging_root, ignore_errors=True)
            raise
    print(f"Captured {manifest_count} requirement entry file(s) in {destination_root}")
    print(f"Copied {copied_count} requirement/constraint file(s), including relative includes")
    print(f"Applied {vcs_rewrite_count} reviewed VCS pin override(s)")
    print(f"Applied {package_rewrite_count} reviewed package replacement(s)")


if __name__ == "__main__":
    main()
