"""The models package: manifests, the library, the streamed download, stamps, the lock, and the author tools."""

from __future__ import annotations

import contextlib
import hashlib
import http.server
import io
import json
import os
import shutil
import tempfile
import threading
import tomllib
import types
import unittest
from pathlib import Path
from unittest import mock

from latentcrate import PROJECT_ROOT, LatentCrateError, models
from latentcrate.models import download, drafts, hub, library
from latentcrate.names import select_sets

CONFIG = PROJECT_ROOT / "config"
MANIFESTS = CONFIG / "model-sets"
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "model-sets"
REAL_DISK_USAGE = shutil.disk_usage
TOKEN = "hf_FAKE_TEST_TOKEN"
COMMIT = "a" * 40
WORKFLOW_URL = f"https://github.com/Comfy-Org/workflow_templates/blob/{COMMIT}/templates/fixture.json"
LICENSE_URL = f"https://huggingface.co/example/model/blob/{COMMIT}/LICENSE"

MANIFEST_HEAD = f"""schema = 1
description = "Fixture"
workflow_urls = ["{WORKFLOW_URL}"]
[[license]]
name = "Test"
url = "{LICENSE_URL}"
"""


def file_row(source: str, destination: str, *, size: str = "1", sha256: str = "a" * 64) -> str:
    return f"""[[file]]
repository = "example/model"
revision = "{COMMIT}"
source = "{source}"
destination = "{destination}"
size = {size}
sha256 = "{sha256}"
"""


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


def serve_bytes(handler, content: bytes, *, honor_range: bool = True, send_only: int | None = None) -> None:
    """Answer a GET with content like a file server: 206 for a satisfiable
    Range, 416 past the end, 200 otherwise. send_only truncates the body
    after the headers promised more, like a dropped connection."""
    body, status, start = content, 200, 0
    range_header = handler.headers.get("Range")
    if range_header and honor_range:
        first, _, last = range_header.removeprefix("bytes=").partition("-")
        start = int(first)
        if start >= len(content):
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{len(content)}")
            handler.end_headers()
            return
        end = int(last) if last else len(content) - 1
        body, status = content[start : end + 1], 206
    handler.send_response(status)
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{start + len(body) - 1}/{len(content)}")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body if send_only is None else body[:send_only])


def serve_json(handler, payload: dict) -> None:
    body = json.dumps(payload).encode()
    handler.send_response(200)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class ModelSetTests(unittest.TestCase):
    def fixture_set(self, content: bytes = b"small pinned model", *, size: int | None = None):
        model_file = models.ModelFile(
            repository="example/models",
            revision=COMMIT,
            source="weights/model.bin",
            destination="diffusion_models/model.bin",
            size=len(content) if size is None else size,
            sha256=hashlib.sha256(content).hexdigest(),
        )
        model_set = models.ModelSet(
            name="fixture",
            description="Fixture",
            workflow_urls=(WORKFLOW_URL,),
            licenses=(models.License("Fixture", LICENSE_URL),),
            files=(model_file,),
        )
        return model_set, model_file

    def read(self, body: str, *, private: bool = False):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "fixture.toml"
            manifest.write_text(body, encoding="utf-8")
            return models.read_manifest(manifest, private=private)

    def unpinned_set(self):
        """A private set whose one file carries neither size nor sha256."""
        model_set, model_file = self.fixture_set()
        model_file = models.ModelFile(**{**model_file.__dict__, "size": None, "sha256": None})
        return models.ModelSet(**{**model_set.__dict__, "files": (model_file,), "private": True}), model_file

    def serve(self, respond) -> int:
        """Start a local HTTP server whose GETs and HEADs call respond(handler); return its port."""

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                respond(self)

            do_HEAD = do_GET

            def log_message(self, *arguments):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def use_hub(self, port: int) -> None:
        """Point the hub module's URL at a local server; the token rule keys on its host."""
        self.addCleanup(setattr, hub, "HUB_URL", hub.HUB_URL)
        hub.HUB_URL = f"http://127.0.0.1:{port}"

    def use_free_space(self, free: int) -> None:
        """Make shutil.disk_usage, which the free-space check consults, report `free` bytes."""
        self.addCleanup(setattr, shutil, "disk_usage", REAL_DISK_USAGE)
        shutil.disk_usage = lambda path: types.SimpleNamespace(free=free)

    def fetch(self, model_set, models_root: Path, token: str | None) -> None:
        with quiet():
            models.fetch([model_set], models_root, models.allowed_roots(models_root, ""), token)

    def status(self, model_set, models_root: Path, *, verify: bool = False) -> int:
        return models.status([model_set], models_root, models.allowed_roots(models_root, ""), verify=verify)

    # Manifests

    def test_shipped_catalog_is_strict_and_complete(self):
        catalog = models.catalog(CONFIG)

        self.assertEqual(set(catalog), {path.stem for path in MANIFESTS.glob("*.toml")})
        self.assertTrue(all(model_set.files and model_set.workflow_urls for model_set in catalog.values()))
        self.assertEqual(models.read_manifest(FIXTURES / "hf-smoke.toml").name, "hf-smoke")

    def test_all_expands_and_shared_files_are_deduplicated(self):
        catalog = models.catalog(CONFIG)
        selected = select_sets(catalog, ["all"], "model set")
        merged = models.merged_files(selected)

        self.assertEqual(len(selected), len(catalog))
        self.assertLess(len(merged), sum(len(item.files) for item in selected))
        self.assertEqual(len({item.destination for item in merged}), len(merged))

    def test_all_is_reserved_as_a_manifest_name(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "all.toml"
            manifest.write_text("schema = 1\n", encoding="utf-8")

            with self.assertRaisesRegex(LatentCrateError, "reserved"):
                models.read_manifest(manifest)

    def test_conflicting_destinations_are_rejected(self):
        model_set, model_file = self.fixture_set()
        conflicting = models.ModelFile(**{**model_file.__dict__, "repository": "other/models", "sha256": "f" * 64})
        other = models.ModelSet("other", "Other", model_set.workflow_urls, model_set.licenses, (conflicting,))

        with self.assertRaisesRegex(LatentCrateError, "disagree"):
            models.merged_files([model_set, other])

    def test_manifest_refusals_name_the_field(self):
        row = file_row("weights/model.bin", "diffusion_models/model.bin")
        self.assertEqual(self.read(MANIFEST_HEAD + row).files[0].destination, "diffusion_models/model.bin")
        refusals = {
            "unknown key.*model_card": 'model_card = "x"\n' + MANIFEST_HEAD + row,
            "schema must be 1": MANIFEST_HEAD.replace("schema = 1", "schema = 2") + row,
            "workflow_urls must contain unique": MANIFEST_HEAD.replace(f"/blob/{COMMIT}/", "/blob/main/") + row,
            "workflow_urls must contain unique, commit-pinned": (
                MANIFEST_HEAD.replace(f'["{WORKFLOW_URL}"]', f'["{WORKFLOW_URL}", "{WORKFLOW_URL}"]') + row
            ),
            "license 1 needs an immutable approved HTTPS URL": MANIFEST_HEAD.replace(LICENSE_URL, "http://x/y") + row,
            "duplicate destination": MANIFEST_HEAD + row + file_row("other.bin", "diffusion_models/model.bin"),
            "sha256 must be lowercase hexadecimal": MANIFEST_HEAD + file_row("a", "b", sha256="A" * 64),
            "size must be a positive integer": MANIFEST_HEAD + file_row("a", "b", size="0"),
            "file 1: destination must be a relative path": MANIFEST_HEAD + file_row("a", "../b"),
            "file 1: source must be a relative path": MANIFEST_HEAD + file_row("../secret", "b"),
            "invalid repository": MANIFEST_HEAD + row.replace('"example/model"', '"../model"'),
            "revision must be a full lowercase commit": MANIFEST_HEAD + row.replace(COMMIT, "main"),
            "unknown field.*file 1: role": MANIFEST_HEAD + row + 'role = "weights"\n',
        }
        for pattern, body in refusals.items():
            with self.subTest(pattern=pattern), self.assertRaisesRegex(LatentCrateError, pattern):
                self.read(body)

    def test_a_private_set_may_leave_the_pins_out_and_a_shipped_set_may_not(self):
        row = file_row("weights/model.bin", "diffusion_models/model.bin")
        unpinned = "\n".join(line for line in row.splitlines() if not line.startswith(("size", "sha256"))) + "\n"

        private = self.read(MANIFEST_HEAD + unpinned, private=True)
        self.assertTrue(private.private)
        self.assertEqual((private.files[0].size, private.files[0].sha256), (None, None))
        self.assertFalse(self.read(MANIFEST_HEAD + row, private=True).files[0].size is None)
        with self.assertRaisesRegex(LatentCrateError, "file 1 size must be a positive integer"):
            self.read(MANIFEST_HEAD + unpinned)
        with self.assertRaisesRegex(LatentCrateError, "file 1 sha256 must be lowercase hexadecimal"):
            self.read(MANIFEST_HEAD + row.replace(f'sha256 = "{"a" * 64}"\n', ""))
        with self.assertRaisesRegex(LatentCrateError, "file 1 size must be a positive integer"):
            self.read(MANIFEST_HEAD + file_row("a", "b", size='"TODO"'), private=True)
        with self.assertRaisesRegex(LatentCrateError, "file 1: destination must be a relative path"):
            self.read(MANIFEST_HEAD + file_row("a", "../b"), private=True)

    def test_the_catalog_adds_private_sets_after_the_shipped_ones_and_tolerates_their_absence(self):
        shipped = models.catalog(CONFIG)
        with tempfile.TemporaryDirectory() as directory:
            local_dir = Path(directory) / "model-sets"
            self.assertEqual(models.catalog(CONFIG, local_dir), shipped)

            local_dir.mkdir()
            (local_dir / "mine.toml").write_text(MANIFEST_HEAD + file_row("w.bin", "loras/w.bin"), encoding="utf-8")
            merged = models.catalog(CONFIG, local_dir)
            self.assertEqual(list(merged), [*shipped, "mine"])
            self.assertTrue(merged["mine"].private)
            self.assertFalse(any(model_set.private for model_set in shipped.values()))

            taken = next(iter(shipped))
            (local_dir / f"{taken}.toml").write_text(MANIFEST_HEAD + file_row("w.bin", "loras/w.bin"), encoding="utf-8")
            with self.assertRaisesRegex(
                LatentCrateError, f"^model set {taken} is declared twice: {MANIFESTS / taken}.toml and {local_dir}/"
            ):
                models.catalog(CONFIG, local_dir)

    # Fetch

    def test_fetch_streams_verifies_then_atomically_publishes(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content)
        requests: list[tuple[str, str | None, str | None]] = []

        def respond(handler):
            requests.append((handler.path, handler.headers.get("Range"), handler.headers.get("Authorization")))
            serve_bytes(handler, content)

        self.use_hub(self.serve(respond))
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            self.fetch(model_set, models_root, TOKEN)
            destination = models_root / model_file.destination
            self.assertEqual(destination.read_bytes(), content)
            self.assertEqual(library.read_stamp(destination), library.stamp_of(destination.stat(), model_file.sha256))
            with quiet():
                self.assertEqual(self.status(model_set, models_root), 0)
            self.assertFalse(list(models_root.rglob(".latentcrate-downloads")))

        # Every request to the Hub asks for the pinned file with the token.
        url_path = f"/example/models/resolve/{COMMIT}/weights/model.bin"
        self.assertTrue(requests)
        self.assertEqual({(path, token) for path, _, token in requests}, {(url_path, f"Bearer {TOKEN}")})

    def test_interrupted_download_resumes_with_a_range_request(self):
        content = bytes(range(256)) * 64
        half = len(content) // 2
        model_set, model_file = self.fixture_set(content)
        ranges: list[str | None] = []
        truncate_first = [half]

        def respond(handler):
            ranges.append(handler.headers.get("Range"))
            if handler.headers.get("Range") == "bytes=0-0":
                serve_bytes(handler, content)
            else:
                serve_bytes(handler, content, send_only=truncate_first.pop() if truncate_first else None)

        self.use_hub(self.serve(respond))
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            with self.assertRaisesRegex(LatentCrateError, "run models fetch again to resume"):
                self.fetch(model_set, models_root, None)
            partial = list(models_root.rglob("*.part"))
            self.assertEqual(len(partial), 1)
            self.assertEqual(partial[0].stat().st_size, half)
            self.assertFalse((models_root / model_file.destination).exists())

            self.fetch(model_set, models_root, None)
            self.assertEqual((models_root / model_file.destination).read_bytes(), content)
            self.assertFalse(list(models_root.rglob(".latentcrate-downloads")))
        # The second run resumes where the first stopped.
        self.assertEqual(ranges[-1], f"bytes={half}-")

    def test_a_full_answer_to_a_range_request_restarts_the_file(self):
        content = bytes(range(256)) * 64
        half = len(content) // 2
        model_set, model_file = self.fixture_set(content)
        data_requests = [dict(send_only=half), dict(honor_range=False)]

        def respond(handler):
            if handler.headers.get("Range") == "bytes=0-0":
                serve_bytes(handler, content)
            else:
                serve_bytes(handler, content, **data_requests.pop(0))

        self.use_hub(self.serve(respond))
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            with self.assertRaisesRegex(LatentCrateError, "run models fetch again to resume"):
                self.fetch(model_set, models_root, None)
            self.fetch(model_set, models_root, None)
            self.assertEqual((models_root / model_file.destination).read_bytes(), content)
        self.assertEqual(data_requests, [])

    def test_a_part_file_holding_every_byte_is_completed_by_a_416(self):
        content = bytes(range(256)) * 64
        model_set, model_file = self.fixture_set(content)
        ranges: list[str | None] = []

        def respond(handler):
            ranges.append(handler.headers.get("Range"))
            serve_bytes(handler, content)

        self.use_hub(self.serve(respond))
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            destination = models_root / model_file.destination
            destination.parent.mkdir(parents=True)
            staging = library.staging_directory(model_file, destination)
            staging.mkdir(parents=True)
            _, part = library.staged_paths(model_file, staging)
            part.write_bytes(content)

            self.fetch(model_set, models_root, None)

            self.assertEqual(destination.read_bytes(), content)
            self.assertFalse(list(models_root.rglob(".latentcrate-downloads")))
        self.assertEqual(ranges[-1], f"bytes={len(content)}-")

    def test_redirect_to_another_host_drops_the_token(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content)
        cdn_requests: list[str | None] = []

        def cdn(handler):
            cdn_requests.append(handler.headers.get("Authorization"))
            serve_bytes(handler, content)

        # Same loopback interface, different host name: the token must stay behind.
        cdn_url = f"http://localhost:{self.serve(cdn)}"
        hub_requests: list[str | None] = []

        def hub_server(handler):
            hub_requests.append(handler.headers.get("Authorization"))
            handler.send_response(302)
            handler.send_header("Location", cdn_url + handler.path)
            handler.end_headers()

        self.use_hub(self.serve(hub_server))
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            self.fetch(model_set, models_root, TOKEN)
            self.assertEqual((models_root / model_file.destination).read_bytes(), content)
        self.assertTrue(hub_requests and cdn_requests)
        self.assertEqual(set(hub_requests), {f"Bearer {TOKEN}"})
        self.assertEqual(set(cdn_requests), {None})

    def test_redirect_off_the_hub_scheme_is_refused_before_any_request(self):
        model_set, _ = self.fixture_set()
        target_requests: list[str | None] = []
        # The Hub fixture speaks http; a redirect to https (or file:) must be
        # refused before a request, and the token with it, reaches the target.
        target_port = self.serve(lambda handler: target_requests.append(handler.headers.get("Authorization")))
        target_url = f"https://localhost:{target_port}"

        def hub_server(handler):
            handler.send_response(302)
            handler.send_header("Location", target_url + handler.path)
            handler.end_headers()

        self.use_hub(self.serve(hub_server))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(LatentCrateError, "refusing a redirect away from http:"):
                self.fetch(model_set, Path(directory), TOKEN)
        self.assertEqual(target_requests, [])

    def test_access_errors_are_reported_before_downloading(self):
        model_set, _ = self.fixture_set()
        statuses = [401, 404]

        def respond(handler):
            handler.send_response(statuses.pop(0))
            handler.end_headers()

        self.use_hub(self.serve(respond))
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            with self.assertRaisesRegex(LatentCrateError, "accept the license and set HF_TOKEN"):
                self.fetch(model_set, models_root, None)
            with self.assertRaisesRegex(LatentCrateError, "does not exist"):
                self.fetch(model_set, models_root, None)
            self.assertFalse(list(models_root.rglob(".latentcrate-downloads")))

    def test_wrong_existing_file_is_never_overwritten(self):
        model_set, model_file = self.fixture_set()
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            destination = models_root / model_file.destination
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"user file")

            with self.assertRaisesRegex(LatentCrateError, "has the wrong size; move it aside"):
                self.fetch(model_set, models_root, None)

            self.assertEqual(destination.read_bytes(), b"user file")

    def test_file_created_during_download_is_not_overwritten(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content)
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            destination = models_root / model_file.destination

            def respond(handler):
                if handler.headers.get("Range") != "bytes=0-0":
                    destination.write_bytes(b"user file created while downloading")
                serve_bytes(handler, content)

            self.use_hub(self.serve(respond))
            with self.assertRaisesRegex(LatentCrateError, "appeared while downloading"):
                self.fetch(model_set, models_root, None)
            self.assertEqual(destination.read_bytes(), b"user file created while downloading")

    def test_a_file_created_between_verification_and_publication_is_not_overwritten(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content)
        self.use_hub(self.serve(lambda handler: serve_bytes(handler, content)))
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            destination = models_root / model_file.destination
            real_link = os.link

            def link_after_a_rival_wrote(source, target):
                Path(target).write_bytes(b"user file")
                real_link(source, target)

            with mock.patch("os.link", side_effect=link_after_a_rival_wrote):
                with self.assertRaisesRegex(LatentCrateError, "appeared while downloading and was not overwritten"):
                    self.fetch(model_set, models_root, None)
            self.assertEqual(destination.read_bytes(), b"user file")
            staged, _ = library.staged_paths(model_file, library.staging_directory(model_file, destination))
            self.assertEqual(staged.read_bytes(), content)

    def test_a_body_whose_length_is_not_the_pinned_size_is_refused_before_a_byte_lands(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content, size=len(content) + 1)
        self.use_hub(self.serve(lambda handler: serve_bytes(handler, content)))
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            refusal = f"the server reports {len(content)} bytes but the manifest pins {len(content) + 1}"
            with self.assertRaisesRegex(LatentCrateError, refusal):
                self.fetch(model_set, models_root, None)
            self.assertEqual(list(models_root.rglob("*.part")), [])
            self.assertFalse((models_root / model_file.destination).exists())

    def test_a_resume_answered_from_another_offset_is_refused_and_the_part_file_kept(self):
        content = bytes(range(256)) * 64
        half = len(content) // 2
        model_set, model_file = self.fixture_set(content)

        def respond(handler):
            if handler.headers.get("Range") == "bytes=0-0":
                serve_bytes(handler, content)
                return
            handler.send_response(206)
            handler.send_header("Content-Range", f"bytes 0-{len(content) - 1}/{len(content)}")
            handler.send_header("Content-Length", str(len(content)))
            handler.end_headers()
            handler.wfile.write(content)

        self.use_hub(self.serve(respond))
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            destination = models_root / model_file.destination
            destination.parent.mkdir(parents=True)
            staging = library.staging_directory(model_file, destination)
            staging.mkdir(parents=True)
            _, part = library.staged_paths(model_file, staging)
            part.write_bytes(content[:half])
            with self.assertRaisesRegex(LatentCrateError, r"resumed at an unexpected offset \(bytes 0-"):
                self.fetch(model_set, models_root, None)
            self.assertEqual(part.read_bytes(), content[:half])
            self.assertFalse(destination.exists())

    def test_integrity_failure_discards_completed_staging(self):
        model_set, model_file = self.fixture_set(b"small pinned model")
        self.use_hub(self.serve(lambda handler: serve_bytes(handler, b"wrong pinned model")))
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            with self.assertRaisesRegex(LatentCrateError, "checksum"):
                self.fetch(model_set, models_root, None)
            self.assertFalse((models_root / model_file.destination).exists())
            self.assertFalse(list(models_root.rglob(".latentcrate-downloads")))

    def test_publication_failure_keeps_the_verified_download_for_the_next_run(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content)
        ranges: list[str | None] = []

        def respond(handler):
            ranges.append(handler.headers.get("Range"))
            serve_bytes(handler, content)

        self.use_hub(self.serve(respond))
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            destination = models_root / model_file.destination

            def refuse_publication(source, target):
                raise PermissionError("read-only")

            with mock.patch("os.link", side_effect=refuse_publication), self.assertRaises(PermissionError):
                self.fetch(model_set, models_root, None)
            self.assertFalse(destination.exists())
            self.assertEqual([path.read_bytes() for path in models_root.rglob("model.bin")], [content])
            seen = len(ranges)

            self.fetch(model_set, models_root, None)

            self.assertEqual(destination.read_bytes(), content)
            self.assertFalse(list(models_root.rglob(".latentcrate-downloads")))
            # The staged file was verified and published without a request.
            self.assertEqual(ranges[seen:], [])

    def test_a_ready_file_removes_a_leftover_staging_directory(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content)
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            destination = self.install(models_root, model_file, content)
            library.staging_directory(model_file, destination).mkdir(parents=True)
            with mock.patch.object(download, "open_url", side_effect=AssertionError("no network")):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    models.fetch([model_set], models_root, models.allowed_roots(models_root, ""), None)
            self.assertIn(f"ready: {model_file.destination}\n", output.getvalue())
            self.assertFalse(list(models_root.rglob(".latentcrate-downloads")))

    def test_the_staging_root_survives_other_downloads_and_reports_a_denied_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / library.STAGING_NAME
            staging, other = root / "one", root / "two"
            staging.mkdir(parents=True)
            other.mkdir()
            library.remove_staging_directory(staging, root)
            self.assertEqual(list(root.iterdir()), [other])
            library.remove_staging_directory(other, root)
            self.assertFalse(root.exists())
            if os.geteuid() == 0:
                self.skipTest("root may remove from a read-only directory")
            parent = Path(directory) / "readonly"
            root = parent / library.STAGING_NAME
            (root / "one").mkdir(parents=True)
            parent.chmod(0o500)
            try:
                with self.assertRaises(PermissionError):
                    library.remove_staging_directory(root / "one", root)
            finally:
                parent.chmod(0o700)

    def test_a_complete_staged_download_needs_neither_space_nor_network(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content)
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            destination = models_root / model_file.destination
            destination.parent.mkdir(parents=True)
            staging = library.staging_directory(model_file, destination)
            staging.mkdir(parents=True)
            staged, _ = library.staged_paths(model_file, staging)
            staged.write_bytes(content)
            self.use_free_space(0)

            with mock.patch.object(download, "open_url", side_effect=AssertionError("no network")):
                self.fetch(model_set, models_root, None)

            self.assertEqual(destination.read_bytes(), content)
            self.assertFalse(list(models_root.rglob(".latentcrate-downloads")))

    def test_free_space_is_checked_before_network_access(self):
        model_set, _ = self.fixture_set()
        requests: list[str] = []
        self.use_hub(self.serve(lambda handler: requests.append(handler.path)))
        self.use_free_space(0)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(LatentCrateError, "are free below .*; make room or fetch fewer sets"):
                self.fetch(model_set, Path(directory), None)
            # A refusal leaves the library as it found it: no category directories.
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(requests, [])

    def test_free_space_counts_a_resumable_part_file_as_downloaded(self):
        # Scaled down: a 17.6 GB file with 17 GB in its .part needs 0.6 GB, not 17.6 GB.
        model_set, model_file = self.fixture_set(size=17_600)
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            destination = models_root / model_file.destination
            destination.parent.mkdir(parents=True)
            self.use_free_space(600)
            with self.assertRaisesRegex(LatentCrateError, "are free"):
                library.require_free_space(models_root, [library.pending_file(model_file, destination)])

            staging = library.staging_directory(model_file, destination)
            staging.mkdir(parents=True)
            _, part = library.staged_paths(model_file, staging)
            part.write_bytes(b"x" * 17_000)
            pending = [library.pending_file(model_file, destination)]
            library.require_free_space(models_root, pending)
            self.use_free_space(599)
            with self.assertRaisesRegex(LatentCrateError, "are free"):
                library.require_free_space(models_root, pending)

    def test_a_second_fetch_into_the_same_library_is_refused(self):
        # flock is per open file description, so a second entry in-process conflicts like a second process.
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            with models.model_download_lock(models_root):
                refusal = "another model-set download is using this models directory"
                with self.assertRaisesRegex(LatentCrateError, refusal), models.model_download_lock(models_root):
                    pass
            with models.model_download_lock(models_root):
                pass
            self.assertEqual(os.listdir(models_root), [".latentcrate-model-set.lock"])

    def test_fetch_rejects_nested_symlinks_in_resumable_staging(self):
        model_set, model_file = self.fixture_set()
        requests: list[str] = []
        self.use_hub(self.serve(lambda handler: requests.append(handler.path) or serve_bytes(handler, b"x")))
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            models_root = Path(directory)
            destination = models_root / model_file.destination
            destination.parent.mkdir(parents=True)
            staging = library.staging_directory(model_file, destination)
            staging.mkdir(parents=True)
            os.symlink(Path(outside), staging / "weights", target_is_directory=True)

            with self.assertRaisesRegex(LatentCrateError, "symlink inside resumable staging"):
                self.fetch(model_set, models_root, None)
        # Only the access check reached the server.
        self.assertEqual(len(requests), 1)

    # Stamps

    def install(self, models_root: Path, model_file, content: bytes) -> Path:
        """Place content at the pinned destination the way another tool would: no stamp."""
        destination = models_root / model_file.destination
        destination.parent.mkdir(parents=True)
        destination.write_bytes(content)
        return destination

    def hashing(self):
        """A wrapper around sha256_file whose call_count says how often a file was read."""
        patcher = mock.patch.object(library, "sha256_file", wraps=library.sha256_file)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_fetch_records_the_verified_file_and_status_trusts_the_record(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content)
        self.use_hub(self.serve(lambda handler: serve_bytes(handler, content)))
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            self.fetch(model_set, models_root, None)
            destination = models_root / model_file.destination
            stamp = library.stamp_path(destination)
            self.assertEqual(stamp.name, "model.bin.latentcrate-verified")
            record = json.loads(stamp.read_text())
            self.assertEqual(record, library.stamp_of(destination.stat(), model_file.sha256))
            self.assertEqual(sorted(record), ["mtime_ns", "sha256", "size"])
            self.assertEqual([path.name for path in destination.parent.iterdir() if path != destination], [stamp.name])

            hashing = self.hashing()
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(self.status(model_set, models_root), 0)
            self.assertEqual(hashing.call_count, 0)
            self.assertNotIn("verifying:", output.getvalue())

    def test_an_unstamped_file_is_hashed_once_and_announced(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content)
        hashing = self.hashing()
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            destination = self.install(models_root, model_file, content)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(self.status(model_set, models_root), 0)
            self.assertEqual(hashing.call_count, 1)
            self.assertIn("verifying: 1 file(s) (0.0 GB) by checksum; later runs trust the record", output.getvalue())
            self.assertEqual(library.read_stamp(destination), library.stamp_of(destination.stat(), model_file.sha256))

            with quiet():
                self.assertEqual(self.status(model_set, models_root), 0)
                self.fetch(model_set, models_root, None)
            self.assertEqual(hashing.call_count, 1)

    def test_a_record_that_disagrees_with_the_file_or_the_manifest_is_rehashed(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content)
        hashing = self.hashing()
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            destination = self.install(models_root, model_file, content)
            good = library.stamp_of(destination.stat(), model_file.sha256)
            stale = [
                good | {"size": good["size"] + 1},
                good | {"mtime_ns": good["mtime_ns"] + 1},
                good | {"sha256": "0" * 64},
            ]
            for index, record in enumerate(stale, start=1):
                library.stamp_path(destination).write_text(json.dumps(record))
                with quiet():
                    self.assertEqual(self.status(model_set, models_root), 0)
                self.assertEqual(hashing.call_count, index)
                self.assertEqual(library.read_stamp(destination), good)

            # A new mtime, which any rewrite of the file leaves behind, is hashed again.
            os.utime(destination, ns=(good["mtime_ns"], good["mtime_ns"] + 1))
            with quiet():
                self.assertEqual(self.status(model_set, models_root), 0)
            self.assertEqual(hashing.call_count, len(stale) + 1)

            library.stamp_path(destination).write_bytes(b"not json")
            with quiet():
                self.assertEqual(self.status(model_set, models_root), 0)
            self.assertEqual(hashing.call_count, len(stale) + 2)
            self.assertEqual(library.read_stamp(destination), library.stamp_of(destination.stat(), model_file.sha256))

    def test_a_failed_stamp_write_leaves_nothing_beside_the_file(self):
        content = b"small pinned model"
        _, model_file = self.fixture_set(content)
        with tempfile.TemporaryDirectory() as directory:
            destination = self.install(Path(directory), model_file, content)
            with mock.patch("os.replace", side_effect=PermissionError("read-only")):
                library.write_stamp(destination, model_file.sha256)
            # The library is left as it was: no stamp and no temporary file.
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_status_verify_catches_a_same_size_swap_that_the_record_misses(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content)
        hashing = self.hashing()
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            destination = self.install(models_root, model_file, content)
            with quiet():
                self.assertEqual(self.status(model_set, models_root), 0)
            record = library.read_stamp(destination)

            # The container mounts the library writable: a same-size rewrite with the timestamp put back.
            destination.write_bytes(b"other pinned model")
            os.utime(destination, ns=(record["mtime_ns"], record["mtime_ns"]))
            with quiet():
                self.assertEqual(self.status(model_set, models_root), 0)
            self.assertEqual(hashing.call_count, 1)

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(self.status(model_set, models_root, verify=True), 1)
            self.assertEqual(hashing.call_count, 2)
            self.assertIn("diffusion_models/model.bin: wrong checksum", output.getvalue())

    def test_a_file_without_pins_is_completed_by_content_length_recorded_and_judged_by_its_record(self):
        content = b"private model nobody else pins"
        model_set, model_file = self.unpinned_set()
        ranges: list[str | None] = []
        self.use_hub(
            self.serve(lambda handler: ranges.append(handler.headers.get("Range")) or serve_bytes(handler, content))
        )
        hashing = self.hashing()
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            destination = models_root / model_file.destination
            # Stale bytes of an earlier attempt: no pin says they belong to the file, so they are not resumed.
            staging = library.staging_directory(model_file, destination)
            staging.mkdir(parents=True)
            library.staged_paths(model_file, staging)[1].write_bytes(b"zzzz")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                models.fetch([model_set], models_root, models.allowed_roots(models_root, ""), None)
            self.assertIn("downloading: diffusion_models/model.bin (size not pinned)", output.getvalue())
            self.assertEqual(ranges, ["bytes=0-0", None])
            self.assertEqual(destination.read_bytes(), content)
            record = library.read_stamp(destination)
            self.assertEqual(record, library.stamp_of(destination.stat(), hashlib.sha256(content).hexdigest()))
            self.assertFalse(list(models_root.rglob(".latentcrate-downloads")))

            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(self.status(model_set, models_root), 0)
            self.assertEqual(output.getvalue(), "diffusion_models/model.bin: recorded\n")
            with quiet():
                self.assertEqual(self.status(model_set, models_root, verify=True), 0)
                self.fetch(model_set, models_root, None)
            self.assertEqual(hashing.call_count, 1)

            # A same-size swap with the timestamp put back passes the record; --verify compares with it.
            destination.write_bytes(b"swapped model nobody else pins")
            os.utime(destination, ns=(record["mtime_ns"], record["mtime_ns"]))
            with quiet():
                self.assertEqual(self.status(model_set, models_root), 0)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(self.status(model_set, models_root, verify=True), 1)
            self.assertEqual(output.getvalue().splitlines()[-1], "diffusion_models/model.bin: changed since recorded")
            # A rewrite that leaves a new mtime is hashed against the record without --verify.
            os.utime(destination, ns=(record["mtime_ns"], record["mtime_ns"] + 1))
            with self.assertRaisesRegex(LatentCrateError, "has the changed since recorded; move it aside"):
                self.fetch(model_set, models_root, None)
            self.assertEqual(destination.read_bytes(), b"swapped model nobody else pins")

    def test_a_present_file_without_pins_or_record_is_unverified_and_never_hashed_or_overwritten(self):
        model_set, model_file = self.unpinned_set()
        hashing = self.hashing()
        requests: list[str] = []
        self.use_hub(self.serve(lambda handler: requests.append(handler.path) or serve_bytes(handler, b"x")))
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            destination = self.install(models_root, model_file, b"placed by hand")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(self.status(model_set, models_root), 1)
            self.assertEqual(output.getvalue(), "diffusion_models/model.bin: unverified\n")
            with self.assertRaisesRegex(LatentCrateError, "present without a record and the manifest pins no checksum"):
                self.fetch(model_set, models_root, None)
            self.assertEqual(destination.read_bytes(), b"placed by hand")
            self.assertIsNone(library.read_stamp(destination))
            library.stamp_path(destination).write_text(json.dumps({"sha256": "nope"}))
            with quiet():
                self.assertEqual(self.status(model_set, models_root, verify=True), 1)
        self.assertEqual((hashing.call_count, requests), (0, []))

    def test_a_server_that_names_no_size_for_an_unpinned_file_is_refused_before_a_byte_lands(self):
        model_set, model_file = self.unpinned_set()

        def respond(handler):
            handler.send_response(200)
            handler.send_header("Transfer-Encoding", "chunked")
            handler.end_headers()
            handler.wfile.write(b"1\r\nx\r\n0\r\n\r\n")

        self.use_hub(self.serve(respond))
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            with self.assertRaisesRegex(LatentCrateError, "pins no size and the server reports none"):
                self.fetch(model_set, models_root, None)
            staging = library.staging_directory(model_file, models_root / model_file.destination)
            self.assertEqual(list(staging.iterdir()), [])

    # Destinations and roots

    def test_status_reports_missing_without_creating_category_directories(self):
        model_set, _ = self.fixture_set()
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(self.status(model_set, models_root), 1)

            self.assertIn("diffusion_models/model.bin: missing", output.getvalue())
            self.assertFalse((models_root / "diffusion_models").exists())

    def test_status_accepts_a_verified_symlink_inside_model_storage(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content)
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            target = models_root / "shared" / "model.bin"
            target.parent.mkdir()
            target.write_bytes(content)
            destination = models_root / model_file.destination
            destination.parent.mkdir(parents=True)
            os.symlink(target, destination)

            with quiet():
                self.assertEqual(self.status(model_set, models_root), 0)

    def test_status_rejects_a_symlink_outside_allowed_storage(self):
        model_set, model_file = self.fixture_set()
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            models_root = Path(directory)
            target = Path(outside) / "model.bin"
            target.write_bytes(b"small pinned model")
            destination = models_root / model_file.destination
            destination.parent.mkdir(parents=True)
            os.symlink(target, destination)

            with self.assertRaisesRegex(LatentCrateError, "symlink escapes.*MODEL_SET_EXTRA_WRITE_ROOTS"):
                self.status(model_set, models_root)

    def test_rejected_directory_symlink_creates_nothing_outside_storage(self):
        model_set, model_file = self.fixture_set()
        nested_file = models.ModelFile(**{**model_file.__dict__, "destination": "diffusion_models/new/model.bin"})
        nested_set = models.ModelSet(
            model_set.name, model_set.description, model_set.workflow_urls, model_set.licenses, (nested_file,)
        )
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            models_root = Path(directory)
            os.symlink(Path(outside), models_root / "diffusion_models", target_is_directory=True)

            with self.assertRaisesRegex(LatentCrateError, "escapes the allowed model storage"):
                self.fetch(nested_set, models_root, None)
            self.assertFalse((Path(outside) / "new").exists())

    def test_a_dangling_directory_symlink_on_the_way_to_a_destination_is_refused(self):
        model_set, model_file = self.fixture_set()
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            os.symlink(models_root / "absent", models_root / "diffusion_models", target_is_directory=True)
            refusal = f"^destination uses a dangling directory symlink: {model_file.destination}$"
            with self.assertRaisesRegex(LatentCrateError, refusal):
                self.fetch(model_set, models_root, None)
            self.assertFalse((models_root / "absent").exists())

    def test_extra_write_roots_admit_a_listed_symlink_target_only(self):
        content = b"small pinned model"
        model_set, model_file = self.fixture_set(content)
        self.use_hub(self.serve(lambda handler: serve_bytes(handler, content)))
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as extra:
            models_root = Path(directory)
            os.symlink(Path(extra), models_root / "diffusion_models", target_is_directory=True)
            with self.assertRaisesRegex(LatentCrateError, "escapes the allowed model storage"):
                self.fetch(model_set, models_root, None)
            self.assertEqual(os.listdir(extra), [])

            roots = models.allowed_roots(models_root, f"{extra}:")
            self.assertEqual(roots, (models_root.resolve(), Path(extra).resolve()))
            with quiet():
                models.fetch([model_set], models_root, roots, None)
                self.assertEqual(models.status([model_set], models_root, roots), 0)
            self.assertEqual((Path(extra) / "model.bin").read_bytes(), content)
            self.assertFalse(list(Path(extra).rglob(".latentcrate-downloads")))

            with self.assertRaisesRegex(LatentCrateError, "not an absolute directory: relative/root"):
                models.allowed_roots(models_root, f"{extra}:relative/root")
            with self.assertRaisesRegex(LatentCrateError, "not an absolute directory: .*model.bin"):
                models.allowed_roots(models_root, str(Path(extra) / "model.bin"))

    # Author tools

    def test_draft_renders_one_file_entry_from_hub_metadata(self):
        payload = {
            "sha": "d" * 40,
            "siblings": [
                {"rfilename": "README.md", "size": 10},
                {"rfilename": "split_files/vae/ae.safetensors", "size": 5, "lfs": {"sha256": "1" * 64}},
            ],
        }
        urls: list[str] = []

        def lookup(url, token):
            urls.append(url)
            return payload

        self.addCleanup(setattr, drafts, "hub_json", drafts.hub_json)
        drafts.hub_json = lookup
        text = models.draft("hf:example/model:split_files/vae/ae.safetensors", None)

        self.assertEqual(urls, [f"{hub.HUB_URL}/api/models/example/model/revision/main?blobs=true"])
        self.assertIn("# TODO: prefix the ComfyUI model folder", text)
        entry = tomllib.loads(text)["file"][0]
        self.assertEqual(
            entry,
            {
                "repository": "example/model",
                "revision": "d" * 40,
                "source": "split_files/vae/ae.safetensors",
                "destination": "ae.safetensors",
                "size": 5,
                "sha256": "1" * 64,
            },
        )
        with self.assertRaisesRegex(LatentCrateError, "is not in example/model"):
            models.draft("hf:example/model:missing.safetensors", None)
        with self.assertRaisesRegex(LatentCrateError, "draft needs"):
            models.draft("hf:../model:m.safetensors", None)

    def test_a_draft_missing_every_hub_field_is_still_valid_toml(self):
        # The Hub does not always report size or sha256; a draft the maintainer
        # cannot parse is worse than one that names the fields to fill in.
        self.addCleanup(setattr, drafts, "hub_json", drafts.hub_json)
        drafts.hub_json = lambda url, token: {"sha": "d" * 40, "siblings": [{"rfilename": "m.safetensors"}]}

        text = models.draft("hf:example/model:m.safetensors", None)

        self.assertEqual(tomllib.loads(text)["file"][0]["size"], "TODO")
        self.assertEqual(tomllib.loads(text)["file"][0]["sha256"], "TODO")

    def test_remote_check_compares_only_the_pins_a_file_carries(self):
        _, model_file = self.fixture_set()
        unpinned = models.ModelFile(**{**model_file.__dict__, "size": None, "sha256": None})
        sized = models.ModelFile(**{**model_file.__dict__, "sha256": None})
        remote = {"size": model_file.size + 1, "lfs": {"sha256": "f" * 64}}
        siblings = {model_file.source: remote}

        self.assertIsNone(drafts.remote_mismatch(unpinned, siblings))
        self.assertEqual(
            drafts.remote_mismatch(sized, siblings), f"remote size {remote['size']}, manifest {sized.size}"
        )
        self.assertEqual(drafts.remote_mismatch(unpinned, {}), "missing from the repository at the pinned revision")

    def test_check_reports_offline_and_compares_with_the_hub_remotely(self):
        model_set, model_file = self.fixture_set()
        other = models.ModelFile(**{**model_file.__dict__, "source": "weights/other.bin", "destination": "o.bin"})
        # A plain Git blob (no LFS record) is judged by its size alone.
        plain = models.ModelFile(**{**model_file.__dict__, "source": "config.json", "destination": "c.json"})
        model_set = models.ModelSet(
            model_set.name,
            model_set.description,
            model_set.workflow_urls,
            model_set.licenses,
            (model_file, other, plain),
        )
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(models.check([model_set], TOKEN, remote=False), 0)
        self.assertEqual(output.getvalue(), "fixture: 3 file(s), 1 license(s)\n")

        requests: list[tuple[str, str, str | None]] = []

        def respond(handler):
            requests.append((handler.command, handler.path, handler.headers.get("Authorization")))
            if handler.path.startswith("/api/models/"):
                serve_json(
                    handler,
                    {
                        "siblings": [
                            {
                                "rfilename": model_file.source,
                                "size": model_file.size,
                                "lfs": {"sha256": model_file.sha256},
                            },
                            {"rfilename": other.source, "size": other.size + 1, "lfs": {"sha256": other.sha256}},
                            {"rfilename": plain.source, "size": plain.size},
                        ]
                    },
                )
                return
            handler.send_response(404 if handler.path.endswith("/LICENSE") else 200)
            handler.end_headers()

        port = self.serve(respond)
        self.use_hub(port)
        real_open_url = hub.open_url

        def open_locally(url, headers, token, *, method):
            """Send the link checks to the local server, keeping their path."""
            host = "/".join(url.split("/", 3)[:3])
            return real_open_url(url.replace(host, f"http://127.0.0.1:{port}", 1), headers, token, method=method)

        with mock.patch.object(drafts, "open_url", open_locally), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(models.check([model_set], TOKEN, remote=True), 1)

        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "fixture: 3 file(s), 1 license(s)",
                "example/models/weights/model.bin: ok",
                f"example/models/weights/other.bin: remote size {other.size + 1}, manifest {other.size}",
                "example/models/config.json: ok",
                f"{WORKFLOW_URL}: ok",
                f"{LICENSE_URL}: HTTP 404",
            ],
        )
        self.assertEqual(
            requests,
            [
                ("GET", f"/api/models/example/models/revision/{COMMIT}?blobs=true", f"Bearer {TOKEN}"),
                ("HEAD", f"/Comfy-Org/workflow_templates/{COMMIT}/templates/fixture.json", f"Bearer {TOKEN}"),
                ("HEAD", f"/example/model/blob/{COMMIT}/LICENSE", f"Bearer {TOKEN}"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
