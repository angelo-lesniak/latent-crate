from __future__ import annotations

import hashlib
import importlib.util
import io
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = ROOT / "services/comfy/install-release-frontend.py"
SPEC = importlib.util.spec_from_file_location("latentcrate_release_frontend", INSTALLER_PATH)
assert SPEC is not None and SPEC.loader is not None
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


def archive_with(member: zipfile.ZipInfo | str, content: bytes = b"content") -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, content)
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


class ReleaseFrontendArchiveTests(unittest.TestCase):
    def test_exact_release_reference_is_required(self) -> None:
        self.assertIsNotNone(
            INSTALLER.RELEASE_RE.fullmatch("Comfy-Org/ComfyUI_frontend@v1.50.4")
        )
        for moving_or_unsafe in (
            "Comfy-Org/ComfyUI_frontend@latest",
            "Comfy-Org/ComfyUI_frontend@1.50.4",
            "../frontend@v1.50.4",
        ):
            self.assertIsNone(INSTALLER.RELEASE_RE.fullmatch(moving_or_unsafe))

    def test_regular_archive_member_is_allowed(self) -> None:
        with archive_with("index.html") as archive:
            INSTALLER.validate_archive(archive)

    def test_parent_traversal_is_rejected(self) -> None:
        with archive_with("../index.html") as archive:
            with self.assertRaises(SystemExit):
                INSTALLER.validate_archive(archive)

    def test_symbolic_link_is_rejected(self) -> None:
        member = zipfile.ZipInfo("link")
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        with archive_with(member, b"index.html") as archive:
            with self.assertRaises(SystemExit):
                INSTALLER.validate_archive(archive)

    def test_expanded_size_limit_is_enforced(self) -> None:
        with archive_with("index.html") as archive:
            with mock.patch.object(INSTALLER, "MAX_EXTRACTED_BYTES", 1):
                with self.assertRaises(SystemExit):
                    INSTALLER.validate_archive(archive)


class FakeResponse:
    """Minimal stand-in for the urlopen response used by download()."""

    def __init__(
        self,
        content: bytes,
        url: str = "https://example.invalid/dist.zip",
        content_length: str | None = None,
    ) -> None:
        self._buffer = io.BytesIO(content)
        self.url = url
        self.headers = {} if content_length is None else {"Content-Length": content_length}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)


def serve(content: bytes, **response_kwargs):
    """Patch urlopen so download() streams the given bytes."""
    return mock.patch.object(
        INSTALLER.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(content, **response_kwargs),
    )


def zip_bytes(member: str = "index.html", content: bytes = b"content") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, content)
    return buffer.getvalue()


class ReleaseFrontendDigestTests(unittest.TestCase):
    def test_matching_digest_is_accepted(self) -> None:
        content = b"release archive bytes"
        digest = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "dist.zip"
            with serve(content):
                INSTALLER.download("https://example.invalid/dist.zip", destination, digest)
            self.assertEqual(destination.read_bytes(), content)

    def test_mismatched_digest_is_rejected(self) -> None:
        content = b"release archive bytes"
        wrong_digest = hashlib.sha256(b"different bytes").hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "dist.zip"
            with serve(content):
                with self.assertRaises(SystemExit) as raised:
                    INSTALLER.download(
                        "https://example.invalid/dist.zip", destination, wrong_digest
                    )
            self.assertIn("digest mismatch", str(raised.exception))

    def test_invalid_digest_is_rejected_before_any_download(self) -> None:
        def refuse_network(request, timeout):
            raise AssertionError("download must not start with an invalid digest")

        for invalid_digest in (
            "abc123",  # far too short
            hashlib.sha256(b"x").hexdigest()[:-1],  # 63 characters
            "z" * 64,  # correct length, not hexadecimal
        ):
            with tempfile.TemporaryDirectory() as temporary:
                destination = Path(temporary) / "frontend"
                argv = [
                    "install-release-frontend.py",
                    "Comfy-Org/ComfyUI_frontend@v1.50.2",
                    str(destination),
                    invalid_digest,
                ]
                with mock.patch.object(sys, "argv", argv):
                    with mock.patch.object(
                        INSTALLER.urllib.request, "urlopen", refuse_network
                    ):
                        with self.assertRaises(SystemExit) as raised:
                            INSTALLER.main()
                self.assertIn("64 hexadecimal characters", str(raised.exception))

    def test_uppercase_digest_is_normalized_and_accepted(self) -> None:
        archive = zip_bytes()
        digest = hashlib.sha256(archive).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "frontend"
            argv = [
                "install-release-frontend.py",
                "Comfy-Org/ComfyUI_frontend@v1.50.2",
                str(destination),
                digest.upper(),
            ]
            with mock.patch.object(sys, "argv", argv):
                with serve(archive):
                    INSTALLER.main()
            self.assertTrue((destination / "index.html").is_file())

    def test_malformed_content_length_is_treated_as_unknown(self) -> None:
        content = b"release archive bytes"
        digest = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "dist.zip"
            with serve(content, content_length="not-a-number"):
                INSTALLER.download("https://example.invalid/dist.zip", destination, digest)
            self.assertEqual(destination.read_bytes(), content)

    def test_streamed_size_limit_applies_without_a_usable_content_length(self) -> None:
        content = b"release archive bytes"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "dist.zip"
            with serve(content, content_length="not-a-number"):
                with mock.patch.object(INSTALLER, "MAX_ARCHIVE_BYTES", len(content) - 1):
                    with self.assertRaises(SystemExit) as raised:
                        INSTALLER.download(
                            "https://example.invalid/dist.zip", destination, ""
                        )
            self.assertIn("exceeds the size limit", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
