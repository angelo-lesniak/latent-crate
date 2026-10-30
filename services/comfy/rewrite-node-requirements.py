#!/usr/bin/env python3
"""Validate and rewrite the saved third-party node requirement tree in place.

Runs inside the node-deps-builder stage over build/custom-node-requirements:
every file named in manifest.txt, and every file those include, is checked
against the reviewed Git hosts, VCS pins, and package replacements, then
rewritten. A tree that was already rewritten is left unchanged.
"""

from __future__ import annotations

import argparse
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn
from urllib.parse import urlsplit

try:
    from packaging.requirements import InvalidRequirement, Requirement
except ModuleNotFoundError:  # pip vendors packaging in the build and test runtimes
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("tree", type=Path)
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
        if not HOST_RE.fullmatch(host):
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
            fail("each [[pin]] must contain only package, repository, requested_ref, and commit")
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
            fail("each [[replacement]] must contain only from, to, drop_extras, and reason")
        source = raw_replacement["from"]
        target = raw_replacement["to"]
        raw_drop_extras = raw_replacement["drop_extras"]
        if not isinstance(source, str) or not NAME_RE.fullmatch(source):
            fail(f"invalid source name in package replacement: {source!r}")
        if not isinstance(target, str) or not NAME_RE.fullmatch(target):
            fail(f"invalid target name in package replacement: {target!r}")
        if not isinstance(raw_drop_extras, list) or any(
            not isinstance(extra, str) or not NAME_RE.fullmatch(extra)
            for extra in raw_drop_extras
        ):
            fail(f"drop_extras must list safe extra names for package replacement {source}")
        canonical_source = package_base(source)
        if canonical_source == package_base(target):
            fail(f"package replacement must change the package name: {source}")
        if canonical_source in replacements:
            fail(f"duplicate package replacement source: {source}")
        replacements[canonical_source] = PackageReplacement(
            source, target, frozenset(extra.lower() for extra in raw_drop_extras)
        )

    for replacement in replacements.values():
        if package_base(replacement.target) in replacements:
            fail(
                f"package replacement chains are not supported: "
                f"{replacement.source} -> {replacement.target}"
            )
    return replacements


def scan_for_secrets(path: Path, text: str) -> None:
    if EMBEDDED_CREDENTIAL_RE.search(text) or TOKEN_RE.search(text):
        fail(f"possible credential found in requirement file: {path}")


def package_base(value: str) -> str:
    return value.split("[", 1)[0].strip().lower().replace("_", "-").replace(".", "-")


def rewrite_vcs_line(
    path: Path,
    line: str,
    pins: list[VcsPin],
    allowed_hosts: set[str],
) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return line
    inspected = re.split(r"\s+#", stripped, maxsplit=1)[0]
    if not VCS_PREFIX_RE.search(inspected):
        return line
    if line.rstrip().endswith("\\"):
        fail(f"multiline VCS requirements are not supported: {path}")

    indent = line[: len(line) - len(line.lstrip())]
    body = stripped
    comment_suffix = ""
    comment_match = re.search(r"\s+#", body)
    if comment_match:
        comment_suffix = body[comment_match.start() :]
        body = body[: comment_match.start()]
    editable = ""
    if body.startswith("-e ") or body.startswith("--editable "):
        editable = "-e "
        body = body.split(maxsplit=1)[1]

    marker_parts = re.split(r"\s*;\s*", body, maxsplit=1)
    body = marker_parts[0]
    marker_suffix = f" ; {marker_parts[1]}" if len(marker_parts) == 2 else ""

    package: str | None = None
    if " @ " in body:
        package, vcs_url = body.split(" @ ", 1)
        package = package.strip()
    else:
        vcs_url = body

    if not vcs_url.startswith("git+https://"):
        fail(f"only git+https VCS requirements are supported: {path}: {line.strip()}")
    parsed = urlsplit(vcs_url[len("git+") :])
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
    normalized = normalize_repository(f"https://{parsed.netloc}{repository_path}", allowed_hosts)
    matching_pin = next((pin for pin in pins if pin.normalized_repository == normalized), None)

    if COMMIT_RE.fullmatch(revision):
        return line
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

    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    return (
        f"{indent}{editable}{package or matching_pin.package} @ "
        f"git+{matching_pin.repository}@{matching_pin.commit}{fragment}"
        f"{marker_suffix}{comment_suffix}"
    )


def rewrite_package_line(
    path: Path,
    line: str,
    replacements: dict[str, PackageReplacement],
) -> str:
    stripped = line.strip()
    if (
        not stripped
        or stripped.startswith("#")
        or INCLUDE_RE.match(stripped)
        or VCS_PREFIX_RE.search(stripped)
        or stripped.startswith("-")
    ):
        return line
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
        return line
    if requirement.url is not None:
        fail(f"package replacements do not support direct URLs: {path}: {body}")

    retained_extras = {
        extra for extra in requirement.extras if extra.lower() not in replacement.drop_extras
    }
    extras = f"[{','.join(sorted(retained_extras))}]" if retained_extras else ""
    marker = f" ; {requirement.marker}" if requirement.marker is not None else ""
    return (
        f"{indent}{replacement.target}{extras}{requirement.specifier}"
        f"{marker}{comment_suffix}"
    )


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
) -> str:
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.endswith("\r\n"):
            content, ending = line[:-2], "\n"
        elif line.endswith(("\n", "\r")):
            content, ending = line[:-1], "\n"
        else:
            content, ending = line, ""
        validate_requirement_line(path, content)
        rewritten = rewrite_vcs_line(path, content, pins, allowed_hosts)
        rewritten = rewrite_package_line(path, rewritten, replacements)
        output.append(rewritten + ending)
    return "".join(output)


def rewrite_tree(
    root: Path,
    pins: list[VcsPin],
    replacements: dict[str, PackageReplacement],
    allowed_hosts: set[str],
) -> int:
    manifest = root / "manifest.txt"
    if not manifest.is_file():
        return 0
    root = root.resolve()
    pending: list[tuple[Path, Path]] = []
    for line in read_text(manifest, "manifest").splitlines():
        if not line:
            continue
        if "\0" in line or Path(line).is_absolute() or ".." in Path(line).parts:
            fail(f"unsafe requirement manifest path: {line!r}")
        pending.append((root / line.split("/", 1)[0], root / line))
    rewritten: set[Path] = set()
    while pending:
        node, path = pending.pop()
        if path.is_symlink() or not path.is_file():
            fail(f"requirement file is missing from the build input: {path}")
        resolved = path.resolve()
        if not resolved.is_relative_to(node):
            fail(f"requirement path escapes its custom-node directory: {path}")
        if resolved in rewritten:
            continue
        text = read_text(path, "requirement file")
        scan_for_secrets(path, text)
        effective = rewrite_requirements(path, text, pins, replacements, allowed_hosts)
        if effective != text:
            path.write_text(effective, encoding="utf-8")
        rewritten.add(resolved)
        for line in text.splitlines():
            match = INCLUDE_RE.match(line)
            if match:
                pending.append((node, path.parent / match.group("path")))
    return len(rewritten)


def main() -> None:
    args = parse_args()
    if not args.tree.is_dir():
        fail(f"custom-node requirement tree is not a directory: {args.tree}")
    allowed_hosts = load_allowed_hosts(read_text(args.allowed_git_hosts, "allowed Git hosts"))
    pins = load_vcs_pins(read_text(args.vcs_pins, "VCS pins"), allowed_hosts)
    replacements = load_package_replacements(
        read_text(args.package_replacements, "package replacements")
    )
    count = rewrite_tree(args.tree, pins, replacements, allowed_hosts)
    print(f"LatentCrate: validated {count} custom-node requirement file(s) in {args.tree}")


if __name__ == "__main__":
    main()
