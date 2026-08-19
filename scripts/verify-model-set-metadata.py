#!/usr/bin/env python3
"""Verify shipped model-set pins against Hugging Face and GitHub metadata."""

from __future__ import annotations

import json
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "LatentCrate-model-set-verifier/1"
WORKFLOW_RE = re.compile(
    r"^https://github\.com/Comfy-Org/workflow_templates/blob/"
    r"(?P<commit>[0-9a-f]{40})/(?P<path>templates/[A-Za-z0-9_.-]+\.json)$"
)
LICENSE_RE = re.compile(
    r"^(?:"
    r"https://huggingface\.co/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/blob/[0-9a-f]{40}/[^\s]+"
    r"|https://cdn\.jsdelivr\.net/gh/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}/[^\s]+"
    r")$"
)


def request_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def verify_workflow(url: str) -> None:
    match = WORKFLOW_RE.fullmatch(url)
    if match is None:
        raise ValueError(f"workflow URL is not an immutable official template: {url}")
    raw_url = (
        "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/"
        f"{match.group('commit')}/{match.group('path')}"
    )
    request = urllib.request.Request(raw_url, headers={"User-Agent": USER_AGENT}, method="HEAD")
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise ValueError(f"workflow returned HTTP {response.status}: {url}")


def verify_reference(url: str, label: str) -> None:
    if label == "license reference" and LICENSE_RE.fullmatch(url) is None:
        raise ValueError(f"license reference is not immutable: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise ValueError(f"{label} returned HTTP {response.status}: {url}")


def main() -> int:
    groups: dict[tuple[str, str], list[tuple[Path, dict]]] = defaultdict(list)
    workflows: set[str] = set()
    licenses: set[str] = set()
    file_count = 0
    for path in sorted((ROOT / "config" / "model-sets").glob("*.toml")):
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        workflows.update(document["workflow_urls"])
        licenses.update(entry["url"] for entry in document["license"])
        for entry in document["file"]:
            groups[(entry["repository"], entry["revision"])].append((path, entry))
            file_count += 1

    try:
        for (repository, revision), entries in groups.items():
            encoded_repository = urllib.parse.quote(repository, safe="/")
            metadata = request_json(
                f"https://huggingface.co/api/models/{encoded_repository}/revision/{revision}?blobs=true"
            )
            siblings = {item["rfilename"]: item for item in metadata.get("siblings", [])}
            for manifest, entry in entries:
                remote = siblings.get(entry["source"])
                context = f"{manifest.relative_to(ROOT)}: {repository}/{entry['source']}"
                if remote is None:
                    raise ValueError(f"remote file is missing: {context}")
                lfs = remote.get("lfs") or {}
                if remote.get("size") != entry["size"]:
                    raise ValueError(f"remote size changed: {context}")
                if lfs.get("sha256") != entry["sha256"]:
                    raise ValueError(f"remote SHA-256 changed or is unavailable: {context}")
        for workflow in sorted(workflows):
            verify_workflow(workflow)
        for license_url in sorted(licenses):
            verify_reference(license_url, "license reference")
    except (KeyError, ValueError, urllib.error.URLError, TimeoutError) as error:
        print(f"model-set metadata verification failed: {error}", file=sys.stderr)
        return 1

    print(
        f"Verified {file_count} model entries at immutable Hugging Face revisions "
        f"plus {len(workflows)} official workflow files and {len(licenses)} immutable license references."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
