#!/usr/bin/env python3
"""Create a deterministic overlay lock from built wheels and a base freeze."""

from __future__ import annotations

import argparse
import email.parser
import re
import zipfile
from pathlib import Path
from typing import NoReturn

NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.!+_-]*$")


def fail(message: str) -> NoReturn:
    raise SystemExit(f"LatentCrate: {message}")


def canonicalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def base_versions(path: Path) -> dict[str, tuple[str, str]]:
    versions: dict[str, tuple[str, str]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        fail(f"could not read base Python environment: {error}")
    for line in lines:
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        if NAME_RE.fullmatch(name) and VERSION_RE.fullmatch(version):
            versions[canonicalize_name(name)] = (name, version)
    return versions


def wheel_identity(path: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            metadata_files = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA") and name.count("/") == 1
            ]
            if len(metadata_files) != 1:
                fail(f"wheel has an unexpected METADATA layout: {path.name}")
            metadata = email.parser.BytesParser().parsebytes(archive.read(metadata_files[0]))
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        fail(f"could not inspect wheel {path.name}: {error}")
    name = metadata.get("Name")
    version = metadata.get("Version")
    if not name or not version or not NAME_RE.fullmatch(name) or not VERSION_RE.fullmatch(version):
        fail(f"wheel metadata is missing a safe Name or Version: {path.name}")
    return name, version


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel_directory", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--base-constraints", required=True, type=Path)
    parser.add_argument("--base-satisfied-output", required=True, type=Path)
    args = parser.parse_args()

    base = base_versions(args.base_constraints)
    identities: dict[str, tuple[str, str]] = {}
    wheels = sorted(args.wheel_directory.glob("*.whl"), key=lambda path: path.name.casefold())
    for wheel in wheels:
        name, version = wheel_identity(wheel)
        canonical = canonicalize_name(name)
        previous = identities.get(canonical)
        if previous:
            fail(
                f"multiple wheels were built for {name}: "
                f"{previous[1]} and {version}"
            )
        identities[canonical] = (name, version)

    overlay: list[tuple[str, str]] = []
    satisfied: list[tuple[str, str]] = []
    for canonical, identity in identities.items():
        base_identity = base.get(canonical)
        if base_identity is None:
            overlay.append(identity)
        elif base_identity[1] == identity[1]:
            satisfied.append(identity)
        else:
            fail(
                f"built wheel {identity[0]}=={identity[1]} conflicts with base "
                f"environment {base_identity[0]}=={base_identity[1]}"
            )

    args.output.write_text(
        "".join(
            f"{name}=={version}\n"
            for name, version in sorted(overlay, key=lambda item: canonicalize_name(item[0]))
        ),
        encoding="utf-8",
    )
    args.base_satisfied_output.write_text(
        "".join(
            f"{name}=={version}\n"
            for name, version in sorted(satisfied, key=lambda item: canonicalize_name(item[0]))
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
