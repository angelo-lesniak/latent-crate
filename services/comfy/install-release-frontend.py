#!/usr/bin/env python3
"""Install an exact ComfyUI frontend release asset without GitHub API calls."""

from __future__ import annotations

import hashlib
import re
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import NoReturn


MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
RELEASE_ASSET_URL_TEMPLATE = (
    "https://github.com/{owner}/{repo}/releases/download/{tag}/dist.zip"
)
RELEASE_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9][A-Za-z0-9-]{0,38})/"
    r"(?P<repo>[A-Za-z0-9_.-]+)@"
    r"(?P<tag>v\d+\.\d+\.\d+(?:[-._A-Za-z0-9]*)?)$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def fail(message: str) -> NoReturn:
    raise SystemExit(f"LatentCrate: {message}")


def validate_archive(archive: zipfile.ZipFile) -> None:
    extracted_bytes = 0
    for member in archive.infolist():
        name = member.filename
        path = PurePosixPath(name)
        mode = member.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if not name or "\\" in name or path.is_absolute() or ".." in path.parts:
            fail(f"unsafe path in frontend archive: {name!r}")
        if stat.S_ISLNK(mode):
            fail(f"symbolic link in frontend archive: {name!r}")
        if file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            fail(f"unsupported file type in frontend archive: {name!r}")
        extracted_bytes += member.file_size
        if extracted_bytes > MAX_EXTRACTED_BYTES:
            fail("frontend release expands beyond the size limit")


def download(url: str, destination: Path, expected_sha256: str = "") -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "LatentCrate-build"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        if response.url.split(":", 1)[0].lower() != "https":
            fail("frontend release redirected away from HTTPS")
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                announced_bytes = int(content_length)
            except ValueError:
                # A malformed header means an unknown length; the streamed
                # limit below still applies.
                announced_bytes = None
            if announced_bytes is not None and announced_bytes > MAX_ARCHIVE_BYTES:
                fail("frontend release archive exceeds the size limit")

        downloaded = 0
        while chunk := response.read(1024 * 1024):
            downloaded += len(chunk)
            if downloaded > MAX_ARCHIVE_BYTES:
                fail("frontend release archive exceeds the size limit")
            digest.update(chunk)
            output.write(chunk)
    if expected_sha256 and digest.hexdigest() != expected_sha256:
        fail(
            "frontend release archive digest mismatch: "
            f"expected sha256 {expected_sha256} but downloaded {digest.hexdigest()}"
        )


def main() -> None:
    if len(sys.argv) not in (3, 4):
        fail(
            "usage: install-release-frontend.py OWNER/REPO@vVERSION DESTINATION"
            " [EXPECTED_DIST_SHA256]"
        )

    match = RELEASE_RE.fullmatch(sys.argv[1])
    if match is None:
        fail("release frontend must be an exact OWNER/REPO@vVERSION reference")

    # An empty digest skips verification; a non-empty one must be a full
    # lowercase-normalized SHA-256 of the release dist.zip asset.
    expected_sha256 = sys.argv[3].strip().lower() if len(sys.argv) == 4 else ""
    if expected_sha256 and not SHA256_RE.fullmatch(expected_sha256):
        fail("expected frontend dist.zip digest must be 64 hexadecimal characters")

    destination = Path(sys.argv[2])
    if destination.exists():
        fail(f"frontend destination already exists: {destination}")

    asset_url = RELEASE_ASSET_URL_TEMPLATE.format(
        owner=match["owner"],
        repo=match["repo"],
        tag=match["tag"],
    )
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="latentcrate-frontend-") as temporary:
        temporary_root = Path(temporary)
        archive_path = temporary_root / "dist.zip"
        staging = temporary_root / "dist"
        staging.mkdir()
        download(asset_url, archive_path, expected_sha256)
        with zipfile.ZipFile(archive_path) as archive:
            validate_archive(archive)
            archive.extractall(staging)
        if not (staging / "index.html").is_file():
            fail("frontend release archive has no root index.html")
        shutil.move(staging, destination)


if __name__ == "__main__":
    main()
