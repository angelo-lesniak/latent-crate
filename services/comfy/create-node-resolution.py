#!/usr/bin/env python3
"""Turn a pip dry-run report into exact requirements for missing packages only."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit

NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.!+_-]*$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def fail(message: str) -> NoReturn:
    raise SystemExit(f"LatentCrate: {message}")


def canonicalize(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def vcs_requirement(name: str, download: object) -> str | None:
    if not isinstance(download, dict) or "vcs_info" not in download:
        return None
    url = download.get("url")
    vcs = download.get("vcs_info")
    subdirectory = download.get("subdirectory")
    if not isinstance(url, str) or not isinstance(vcs, dict) or vcs.get("vcs") != "git":
        fail(f"unsupported VCS resolution for {name}")
    commit = vcs.get("commit_id")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or not isinstance(commit, str)
        or not COMMIT_RE.fullmatch(commit)
    ):
        fail(f"unsafe VCS resolution for {name}")
    fragment = ""
    if subdirectory is not None:
        if not isinstance(subdirectory, str) or not re.fullmatch(
            r"[A-Za-z0-9._/-]+", subdirectory
        ) or ".." in Path(subdirectory).parts:
            fail(f"unsafe VCS subdirectory for {name}")
        fragment = f"#subdirectory={subdirectory}"
    return f"{name} @ git+{url}@{commit.lower()}{fragment}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"could not read pip resolution report: {error}")
    installs = report.get("install")
    if not isinstance(installs, list):
        fail("pip resolution report has no install list")

    requirements: dict[str, str] = {}
    for item in installs:
        if not isinstance(item, dict) or not isinstance(item.get("metadata"), dict):
            fail("pip resolution report contains an invalid install entry")
        name = item["metadata"].get("name")
        version = item["metadata"].get("version")
        if (
            not isinstance(name, str)
            or not NAME_RE.fullmatch(name)
            or not isinstance(version, str)
            or not VERSION_RE.fullmatch(version)
        ):
            fail("pip resolution report contains an unsafe package identity")
        requirement = vcs_requirement(name, item.get("download_info")) or f"{name}=={version}"
        canonical = canonicalize(name)
        if canonical in requirements:
            fail(f"pip resolution contains duplicate package {name}")
        requirements[canonical] = requirement

    args.output.write_text(
        "".join(f"{requirements[name]}\n" for name in sorted(requirements)),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
