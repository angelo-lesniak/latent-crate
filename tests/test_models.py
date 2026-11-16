"""The models package: manifests, the library, the streamed download, stamps, the lock, and the author tools."""

from __future__ import annotations

import contextlib
import email.message
import hashlib
import http.server
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import tomllib
import types
import unittest
import urllib.error
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
CIVITAI_TOKEN = "civitai_FAKE_TEST_TOKEN"
VERSION = 4242
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


def serve_bytes(
    handler,
    content: bytes,
    *,
    honor_range: bool = True,
    send_only: int | None = None,
    extra_headers: dict | None = None,
) -> None:
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
    for key, value in (extra_headers or {}).items():
        handler.send_header(key, value)
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

    def civitai_set(self, content: bytes = b"small civitai model", *, private: bool = False):
        model_set, model_file = self.fixture_set(content)
        model_file = models.ModelFile(
            None, None, None, "loras/style.safetensors", len(content), model_file.sha256, civitai_version=VERSION
        )
        return models.ModelSet(**{**model_set.__dict__, "files": (model_file,), "private": private}), model_file

    def serve_civitai(self, download, requests: list | None = None) -> None:
        """Serve Civitai: its record of VERSION (primary file id 9), and download(handler) for anything else."""

        def respond(handler):
            if requests is not None:
                requests.append((handler.path, handler.headers.get("Authorization")))
            if handler.path == f"/api/v1/model-versions/{VERSION}":
                serve_json(handler, {"files": [{"id": 8, "primary": False}, {"id": 9, "primary": True}]})
            else:
                download(handler)

        self.use_civitai(self.serve(respond))

    def use_civitai(self, port: int) -> None:
        """Point the Civitai URL at a local server on its own port, so it is an origin apart from the Hub's."""
        self.addCleanup(setattr, hub, "CIVITAI_URL", hub.CIVITAI_URL)
        hub.CIVITAI_URL = f"http://127.0.0.1:{port}"

    def use_hub(self, port: int) -> None:
        """Point the hub module's URL at a local server; the token rule keys on its host."""
        self.addCleanup(setattr, hub, "HUB_URL", hub.HUB_URL)
        hub.HUB_URL = f"http://127.0.0.1:{port}"

    def serve_links(self) -> list[tuple[str, str, str | None]]:
        """Answer the HEAD of every workflow and license link from a local
        server that is no provider's origin, 404 for a LICENSE; return its
        (method, path, Authorization) requests."""
        requests: list[tuple[str, str, str | None]] = []

        def respond(handler):
            requests.append((handler.command, handler.path, handler.headers.get("Authorization")))
            handler.send_response(404 if handler.path.endswith("/LICENSE") else 200)
            handler.end_headers()

        port = self.serve(respond)
        real_open_url = hub.open_url

        def open_locally(url, headers, tokens, *, method):
            """Send a link check to the local server, keeping its path."""
            host = "/".join(url.split("/", 3)[:3])
            return real_open_url(url.replace(host, f"http://127.0.0.1:{port}", 1), headers, tokens, method=method)

        patcher = mock.patch.object(drafts, "open_url", open_locally)
        patcher.start()
        self.addCleanup(patcher.stop)
        return requests

    def use_free_space(self, free: int) -> None:
        """Make shutil.disk_usage, which the free-space check consults, report `free` bytes."""
        self.addCleanup(setattr, shutil, "disk_usage", REAL_DISK_USAGE)
        shutil.disk_usage = lambda path: types.SimpleNamespace(free=free)

    def fetch(self, model_set, models_root: Path, token: str | None, *, civitai: str | None = None) -> None:
        with quiet():
            models.fetch([model_set], models_root, models.allowed_roots(models_root, ""), models.Tokens(token, civitai))

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

    def test_a_destination_may_not_take_a_name_the_library_reserves(self):
        for destination in (
            ".latentcrate-model-set.lock",
            "loras/.latentcrate-downloads/x.bin",
            "loras/x.bin.latentcrate-verified",
            "loras/.latentcrate-verified",
        ):
            with self.subTest(destination=destination), self.assertRaises(LatentCrateError) as refusal:
                self.read(MANIFEST_HEAD + file_row("a", destination))
            self.assertEqual(
                str(refusal.exception),
                f"fixture: file 1 destination uses a name LatentCrate reserves: {destination}",
            )

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

    def test_a_civitai_file_names_a_version_instead_of_a_repository(self):
        row = f'[[file]]\ncivitai_version = {VERSION}\ndestination = "loras/s.safetensors"\n'
        pins = f'size = 1\nsha256 = "{"a" * 64}"\n'
        model_file = self.read(MANIFEST_HEAD + row + pins).files[0]
        self.assertEqual((model_file.civitai_version, model_file.repository, model_file.source), (VERSION, None, None))
        self.assertIsNone(self.read(MANIFEST_HEAD + row, private=True).files[0].sha256)
        with self.assertRaisesRegex(LatentCrateError, "file 1 size must be a positive integer"):
            self.read(MANIFEST_HEAD + row)
        for value in ("0", "-1", "true", '"4242"', "1.5"):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(LatentCrateError, "civitai_version must be a positive"),
            ):
                self.read(MANIFEST_HEAD + row.replace(str(VERSION), value) + pins)
        for extra in ('repository = "example/model"\n', f'revision = "{COMMIT}"\n', 'source = "a.bin"\n'):
            with (
                self.subTest(extra=extra),
                self.assertRaisesRegex(LatentCrateError, "civitai_version beside repository"),
            ):
                self.read(MANIFEST_HEAD + row + pins + extra)
        with self.assertRaisesRegex(LatentCrateError, "unknown field.*download_url"):
            self.read(MANIFEST_HEAD + row + pins + 'download_url = "https://evil.example/x"\n')
        self.assertEqual(self.read(MANIFEST_HEAD + row + pins + "civitai_file = 8\n").files[0].civitai_file, 8)
        self.assertIsNone(model_file.civitai_file)
        for value in ("0", "-1", "true", '"8"', "1.5"):
            with (
                self.subTest(civitai_file=value),
                self.assertRaisesRegex(LatentCrateError, "civitai_file must be a positive"),
            ):
                self.read(MANIFEST_HEAD + row + pins + f"civitai_file = {value}\n")
        with self.assertRaisesRegex(LatentCrateError, "file 1 names civitai_file without civitai_version"):
            self.read(MANIFEST_HEAD + file_row("a", "b") + "civitai_file = 8\n")

        # A model page is mutable, so only a private set may cite it as its license.
        url = "https://civitai.com/models/77"
        page = MANIFEST_HEAD.replace(LICENSE_URL, url)
        self.assertEqual(self.read(page + row, private=True).licenses[0].url, url)
        with self.assertRaisesRegex(LatentCrateError, "license 1 needs an immutable approved HTTPS URL"):
            self.read(page + row + pins)
        for url in (
            "https://civitai.red/models/77",
            "https://civitai.green/models/77",
            "https://civitai.com/models/77?x=1",
            "http://civitai.com/models/77",
            "https://civitai.com/models/0",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(LatentCrateError, "license 1 needs"):
                self.read(MANIFEST_HEAD.replace(LICENSE_URL, url) + row, private=True)
        # A pinned URL names one file of the pinned tree, and nothing else.
        base = f"https://github.com/example/model/blob/{COMMIT}"
        self.assertEqual(
            self.read(MANIFEST_HEAD.replace(LICENSE_URL, f"{base}/..x/L") + row + pins).licenses[0].url, f"{base}/..x/L"
        )
        for suffix in (
            "../LICENSE",
            "a/./LICENSE",
            "%2e%2e/LICENSE",
            "%2E./LICENSE",
            "LICENSE?plain=1",
            "LICENSE#L1",
            # TOML-escaped: a\..\..\LICENSE
            "a\\\\..\\\\..\\\\LICENSE",
        ):
            with self.subTest(suffix=suffix), self.assertRaisesRegex(LatentCrateError, "license 1 needs"):
                self.read(MANIFEST_HEAD.replace(LICENSE_URL, f"{base}/{suffix}") + row)

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

    def test_each_token_goes_to_its_own_origin_only(self):
        tokens = models.Tokens(TOKEN, CIVITAI_TOKEN)
        cases = {
            "https://huggingface.co/a/b/resolve/c/d": TOKEN,
            "https://civitai.com/api/download/models/1": CIVITAI_TOKEN,
            "https://CIVITAI.com/api/v1/model-versions/1": CIVITAI_TOKEN,
            "http://civitai.com/api/download/models/1": None,
            "https://civitai.com:8443/api/download/models/1": None,
            "https://civitai.red/api/download/models/1": CIVITAI_TOKEN,
            "https://civitai.com.example/x": None,
            "https://civitai.green/x": None,
            "https://b2.civitai.com/file/x": None,
            "https://delivery.civitai.com/x": None,
            "https://user@example.com/x": None,
            "https://cdn-lfs.huggingface.co/x": None,
        }
        for url, token in cases.items():
            with self.subTest(url=url):
                self.assertEqual(tokens.for_url(url), token)

    def test_a_token_goes_only_to_the_provider_the_request_started_at(self):
        seen: dict[str, list[str | None]] = {"hub": [], "civitai": [], "other": []}

        def host(name):
            def respond(handler):
                seen[name].append(handler.headers.get("Authorization"))
                target = {"/to-hub": hub.HUB_URL, "/to-civitai": hub.CIVITAI_URL}.get(handler.path)
                handler.send_response(302 if target else 200)
                if target:
                    handler.send_header("Location", f"{target}/end")
                handler.end_headers()

            return self.serve(respond)

        self.use_hub(host("hub"))
        self.use_civitai(host("civitai"))
        other = f"http://127.0.0.1:{host('other')}"
        tokens = models.Tokens(TOKEN, CIVITAI_TOKEN)
        cases = [
            (f"{hub.HUB_URL}/to-civitai", {"hub": [f"Bearer {TOKEN}"], "civitai": [None], "other": []}),
            (f"{hub.CIVITAI_URL}/to-hub", {"hub": [None], "civitai": [f"Bearer {CIVITAI_TOKEN}"], "other": []}),
            (f"{other}/to-hub", {"hub": [None], "civitai": [], "other": [None]}),
            (f"{other}/to-civitai", {"hub": [], "civitai": [None], "other": [None]}),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                for requests in seen.values():
                    requests.clear()
                hub.open_url(url, {}, tokens).close()
                self.assertEqual(seen, expected)

    def test_a_redirect_from_https_to_http_is_refused_before_any_request(self):
        headers = email.message.Message()
        headers["Location"] = "http://cdn.example/file?X-Signature=signed-secret"
        found = urllib.error.HTTPError("https://civitai.com/x", 302, "Found", headers, None)
        with (
            mock.patch.object(hub.OPENER, "open", side_effect=found) as opened,
            self.assertRaises(LatentCrateError) as refusal,
        ):
            hub.open_url("https://civitai.com/x", {}, models.Tokens(TOKEN, CIVITAI_TOKEN))
        self.assertEqual(str(refusal.exception), "refusing a redirect away from https: http://cdn.example/file")
        self.assertEqual(opened.call_count, 1)

    def test_a_redirect_loop_stops_at_the_cap_without_the_signed_query(self):
        requests: list[str] = []

        def respond(handler):
            requests.append(handler.path)
            # Past the cap the loop ends, so a missing cap shows as a download, not a hang.
            if len(requests) > hub.MAX_REDIRECTS + 5:
                serve_bytes(handler, b"x")
                return
            handler.send_response(302)
            handler.send_header("Location", "/loop?X-Signature=signed-secret")
            handler.end_headers()

        self.use_civitai(self.serve(respond))
        with self.assertRaises(LatentCrateError) as refusal:
            hub.open_url(f"{hub.CIVITAI_URL}/start", {}, models.Tokens(None, CIVITAI_TOKEN))
        self.assertEqual(str(refusal.exception), f"too many redirects for {hub.CIVITAI_URL}/loop")
        self.assertEqual(len(requests), hub.MAX_REDIRECTS + 1)

    def test_host_failures_name_neither_the_token_nor_a_signed_query(self):
        model_set, model_file = self.fixture_set()
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        unreachable = f"http://127.0.0.1:{closed.getsockname()[1]}"
        closed.close()
        cdn = {
            "/500": lambda handler: handler.send_response(500) or handler.end_headers(),
            "/text": lambda handler: serve_bytes(handler, b"<html>not json</html>"),
            "/list": lambda handler: serve_bytes(handler, b"[]"),
        }
        cdn_url = f"http://localhost:{self.serve(lambda handler: cdn[handler.path.partition('?')[0]](handler))}"
        target: list[str] = []

        def hub_server(handler):
            handler.send_response(302)
            handler.send_header("Location", f"{target[0]}?X-Signature=signed-secret")
            handler.end_headers()

        self.use_hub(self.serve(hub_server))
        revision = hub.revision_url(model_file.repository, COMMIT)
        where = model_file.origin
        cases = [
            (
                f"{cdn_url}/500",
                lambda: hub.check_access(model_file, models.Tokens(TOKEN)),
                f"^Hugging Face returned HTTP 500 for {where}$",
            ),
            (
                unreachable,
                lambda: hub.check_access(model_file, models.Tokens(TOKEN)),
                f"^could not reach Hugging Face for {where}: ",
            ),
            (
                f"{cdn_url}/500",
                lambda: hub.hub_json(revision, models.Tokens(TOKEN)),
                f"^Hugging Face returned HTTP 500 for {hub.HUB_URL}/api/models/",
            ),
            (
                f"{cdn_url}/text",
                lambda: hub.hub_json(revision, models.Tokens(TOKEN)),
                f"^Hugging Face answered {hub.HUB_URL}/api/models/.* with something other than JSON$",
            ),
            (
                f"{cdn_url}/list",
                lambda: hub.hub_json(revision, models.Tokens(TOKEN)),
                f"^unexpected Hugging Face response for {hub.HUB_URL}/api/models/",
            ),
            (unreachable, lambda: hub.hub_json(revision, models.Tokens(TOKEN)), "^could not query Hugging Face: "),
        ]
        for location, call, pattern in cases:
            with self.subTest(location=location, pattern=pattern):
                target[:] = [location]
                with self.assertRaisesRegex(LatentCrateError, pattern) as refusal:
                    call()
                self.assertNotIn("signed-secret", str(refusal.exception))
                self.assertNotIn(TOKEN, str(refusal.exception))

    def civitai_download_chain(self):
        """Fetch a Civitai file through civitai.com, a 307 to civitai.red, and a
        307 to a CDN that offers its own file name; return what each host saw
        and the files left on disk."""
        content = b"small civitai model"
        model_set, _ = self.civitai_set(content)
        seen = types.SimpleNamespace(civitai=[], civitai_download=[], red=[], cdn=[], hub=[])

        def record(requests, handler):
            requests.append((handler.path, handler.headers.get("Authorization"), handler.headers.get("User-Agent")))

        def cdn(handler):
            record(seen.cdn, handler)
            serve_bytes(
                handler, content, extra_headers={"Content-Disposition": 'attachment; filename="../../evil.bin"'}
            )

        cdn_url = f"http://localhost:{self.serve(cdn)}"

        def red(handler):
            record(seen.red, handler)
            handler.send_response(307)
            handler.send_header("Location", f"{cdn_url}/file?X-Signature=signed")
            handler.end_headers()

        self.addCleanup(setattr, hub, "CIVITAI_RED_URL", hub.CIVITAI_RED_URL)
        hub.CIVITAI_RED_URL = f"http://127.0.0.1:{self.serve(red)}"

        def civitai(handler):
            record(seen.civitai_download, handler)
            handler.send_response(307)
            handler.send_header("Location", f"{hub.CIVITAI_RED_URL}{handler.path}")
            handler.end_headers()

        self.use_hub(self.serve(lambda handler: record(seen.hub, handler)))
        self.serve_civitai(civitai, seen.civitai)
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory) / "models"
            models_root.mkdir()
            self.fetch(model_set, models_root, TOKEN, civitai=CIVITAI_TOKEN)
            seen.content = (models_root / "loras/style.safetensors").read_bytes()
            seen.files = sorted(
                str(path.relative_to(directory)) for path in Path(directory).rglob("*") if path.is_file()
            )
        return seen

    def test_a_civitai_download_sends_its_token_to_civitai_and_civitai_red_only(self):
        seen = self.civitai_download_chain()
        self.assertEqual({token for _, token in seen.civitai}, {f"Bearer {CIVITAI_TOKEN}"})
        # Civitai's mature-content host shares the account, so the token follows a redirect there.
        self.assertEqual({token for _, token, _ in seen.red}, {f"Bearer {CIVITAI_TOKEN}"})
        self.assertEqual(seen.hub, [])

    def test_the_civitai_cdn_gets_no_token(self):
        seen = self.civitai_download_chain()
        self.assertEqual({(path, token) for path, token, _ in seen.cdn}, {("/file?X-Signature=signed", None)})

    def test_a_civitai_file_is_asked_for_by_id_and_the_name_the_server_offers_is_ignored(self):
        seen = self.civitai_download_chain()
        # By id, so the account's file preferences cannot pick another file.
        self.assertEqual(
            sorted({path for path, _ in seen.civitai}),
            [f"/api/download/models/{VERSION}?fileId=9", f"/api/v1/model-versions/{VERSION}"],
        )
        self.assertEqual(seen.content, b"small civitai model")
        self.assertEqual(
            seen.files, ["models/loras/style.safetensors", "models/loras/style.safetensors.latentcrate-verified"]
        )

    def test_every_hop_sends_the_package_user_agent(self):
        seen = self.civitai_download_chain()
        # Cloudflare in front of Civitai refuses urllib's default agent.
        self.assertTrue(seen.civitai_download and seen.red and seen.cdn)
        self.assertEqual({agent for _, _, agent in seen.civitai_download + seen.red + seen.cdn}, {hub.USER_AGENT})

    def test_civitai_file_downloads_that_file_of_the_version(self):
        content = b"small civitai model"
        model_set, model_file = self.civitai_set(content)
        chosen = models.ModelFile(**{**model_file.__dict__, "civitai_file": 8})
        requests: list[tuple[str, str | None]] = []
        self.serve_civitai(lambda handler: serve_bytes(handler, content), requests)
        with tempfile.TemporaryDirectory() as directory:
            self.fetch(models.ModelSet(**{**model_set.__dict__, "files": (chosen,)}), Path(directory), None)
            self.assertEqual((Path(directory) / chosen.destination).read_bytes(), content)
        self.assertIn((f"/api/download/models/{VERSION}?fileId=8", None), requests)
        self.assertNotEqual(
            library.staging_directory(chosen, Path("/m/x")), library.staging_directory(model_file, Path("/m/x"))
        )
        missing = models.ModelFile(**{**model_file.__dict__, "civitai_file": 7})
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(LatentCrateError, f"^Civitai model version {VERSION} has no file 7$"),
        ):
            self.fetch(models.ModelSet(**{**model_set.__dict__, "files": (missing,)}), Path(directory), None)

    def test_a_civitai_file_without_a_positive_integer_id_is_refused_before_downloading(self):
        model_set, _ = self.civitai_set()
        requests: list[str] = []

        def respond(handler):
            requests.append(handler.path)
            serve_json(handler, {"files": [file]})

        self.use_civitai(self.serve(respond))
        for file in ({"primary": True}, {"id": True}, {"id": 0}, {"id": -9}, {"id": "9"}):
            requests.clear()
            with self.subTest(file=file), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(
                    LatentCrateError, f"^Civitai names no file id for the primary file of model version {VERSION}$"
                ):
                    self.fetch(model_set, Path(directory), None)
                self.assertEqual(requests, [f"/api/v1/model-versions/{VERSION}"])
        # Only `primary: true` marks the primary file.
        self.assertEqual(
            hub.version_file(VERSION, {"files": [{"id": 8, "primary": 1}, {"id": 9, "primary": True}]})["id"], 9
        )

    def test_a_civitai_redirect_off_its_scheme_is_refused_without_the_signed_query(self):
        model_set, _ = self.civitai_set()
        target_requests: list[str | None] = []
        target_port = self.serve(lambda handler: target_requests.append(handler.headers.get("Authorization")))

        def civitai(handler):
            handler.send_response(307)
            handler.send_header("Location", f"https://localhost:{target_port}/file?X-Signature=signed-secret")
            handler.end_headers()

        self.serve_civitai(civitai)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(LatentCrateError, "refusing a redirect away from http:") as refusal:
                self.fetch(model_set, Path(directory), None, civitai=CIVITAI_TOKEN)
        self.assertEqual(
            str(refusal.exception), f"refusing a redirect away from http: https://localhost:{target_port}/file"
        )
        self.assertEqual(target_requests, [])

    def test_a_malformed_redirect_is_refused_without_its_query(self):
        model_set, _ = self.civitai_set()
        locations = [
            "https://cdn.example/a b?X-Signature=signed-secret",
            "http://cdn.example:x/f?X-Signature=signed-secret",
            "http://[::1/f?X-Signature=signed-secret",
            "http://evil.example\\@127.0.0.1/f?X-Signature=signed-secret",
            "http://user@cdn.example/f?X-Signature=signed-secret",
            "/f\\g?X-Signature=signed-secret",
            # http.client would refuse it with the whole URL in its message.
            "/f\x01?X-Signature=signed-secret",
            "/f\u00e9?X-Signature=signed-secret",
        ]

        def civitai(handler):
            handler.send_response(307)
            handler.send_header("Location", locations[0])
            handler.end_headers()

        self.serve_civitai(civitai)
        while locations:
            with self.subTest(location=locations[0]), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(
                    LatentCrateError, "^refusing a malformed redirect from http://127"
                ) as refusal:
                    self.fetch(model_set, Path(directory), None, civitai=CIVITAI_TOKEN)
                self.assertNotIn("signed-secret", str(refusal.exception))
            locations.pop(0)
        self.assertNotIn(CIVITAI_TOKEN, repr(models.Tokens(TOKEN, CIVITAI_TOKEN)) + repr(models.Tokens(TOKEN)))
        self.assertNotIn(TOKEN, repr(models.Tokens(TOKEN, CIVITAI_TOKEN)))

    def test_civitai_access_refusals_name_the_token_or_the_account(self):
        model_set, _ = self.civitai_set()
        statuses = [401, 403, 404]
        seen: list[str | None] = []

        def respond(handler):
            seen.append(handler.headers.get("Authorization"))
            handler.send_response(statuses.pop(0))
            handler.end_headers()

        self.serve_civitai(respond)
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory)
            with self.assertRaisesRegex(
                LatentCrateError,
                f"^cannot access Civitai model version {VERSION} \\(HTTP 401\\).*set CIVITAI_TOKEN in .env",
            ):
                self.fetch(model_set, models_root, TOKEN)
            with self.assertRaises(LatentCrateError) as refusal:
                self.fetch(model_set, models_root, None, civitai=CIVITAI_TOKEN)
            self.assertIn("(HTTP 403) with CIVITAI_TOKEN", str(refusal.exception))
            self.assertIn(f"{hub.CIVITAI_URL}/model-versions/{VERSION}", str(refusal.exception))
            self.assertIn("your account may download it", str(refusal.exception))
            self.assertNotIn(CIVITAI_TOKEN, str(refusal.exception))
            with self.assertRaisesRegex(LatentCrateError, "does not exist: the version or its file was removed"):
                self.fetch(model_set, models_root, None, civitai=CIVITAI_TOKEN)
            self.assertFalse(list(models_root.rglob(".latentcrate-downloads")))
        self.assertEqual(seen, [None, f"Bearer {CIVITAI_TOKEN}", f"Bearer {CIVITAI_TOKEN}"])

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
                    models.fetch([model_set], models_root, models.allowed_roots(models_root, ""), models.Tokens())
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
                models.fetch([model_set], models_root, models.allowed_roots(models_root, ""), models.Tokens())
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

    def test_a_stopped_download_without_a_pinned_size_says_the_next_fetch_starts_over(self):
        model_set, model_file = self.unpinned_set()
        self.use_hub(self.serve(lambda handler: serve_bytes(handler, b"private model", send_only=3)))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                LatentCrateError,
                f"^download of {model_file.destination} stopped after 3 of 13 bytes; "
                "run models fetch again to start over$",
            ):
                self.fetch(model_set, Path(directory), None)

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
                models.fetch([model_set], models_root, roots, models.Tokens())
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

        def lookup(url, tokens):
            urls.append(url)
            return payload

        self.addCleanup(setattr, drafts, "hub_json", drafts.hub_json)
        drafts.hub_json = lookup
        text = models.draft("hf:example/model:split_files/vae/ae.safetensors", models.Tokens())

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
            models.draft("hf:example/model:missing.safetensors", models.Tokens())
        with self.assertRaisesRegex(LatentCrateError, "draft needs"):
            models.draft("hf:../model:m.safetensors", models.Tokens())

    def test_a_draft_missing_every_hub_field_is_still_valid_toml(self):
        # The Hub does not always report size or sha256; a draft the maintainer
        # cannot parse is worse than one that names the fields to fill in.
        self.addCleanup(setattr, drafts, "hub_json", drafts.hub_json)
        drafts.hub_json = lambda url, tokens: {"sha": "d" * 40, "siblings": [{"rfilename": "m.safetensors"}]}

        text = models.draft("hf:example/model:m.safetensors", models.Tokens())

        self.assertEqual(tomllib.loads(text)["file"][0]["size"], "TODO")
        self.assertEqual(tomllib.loads(text)["file"][0]["sha256"], "TODO")

    def civitai_api(self, payload: dict) -> list[tuple[str, str | None]]:
        """Serve payload as Civitai's model-version record; return the requests it saw."""
        requests: list[tuple[str, str | None]] = []

        def respond(handler):
            requests.append((handler.path, handler.headers.get("Authorization")))
            serve_json(handler, payload)

        self.use_civitai(self.serve(respond))
        return requests

    def test_draft_renders_the_primary_file_of_a_civitai_version(self):
        content = b"small civitai model" * 100
        files = [
            {"id": 8, "name": "data.zip", "type": "Training Data", "primary": False, "sizeKB": 1.0},
            {
                "id": 9,
                "name": "style.safetensors",
                "primary": True,
                "sizeKB": len(content) / 1024,
                "hashes": {"SHA256": hashlib.sha256(content).hexdigest().upper(), "AutoV2": "ABCDEF"},
            },
        ]
        requests = self.civitai_api({"id": VERSION, "modelId": 77, "files": files})
        text = models.draft(f"civitai:{VERSION}", models.Tokens(TOKEN, CIVITAI_TOKEN))

        self.assertEqual(requests, [(f"/api/v1/model-versions/{VERSION}", f"Bearer {CIVITAI_TOKEN}")])
        self.assertIn(f"# Model page and license: {hub.CIVITAI_URL}/models/77\n", text)
        self.assertTrue(text.endswith("\n#   civitai_file = 8: 'data.zip', size 1024, sha256 unknown\n"))
        self.assertEqual(
            tomllib.loads(text)["file"][0],
            {
                "civitai_version": VERSION,
                "destination": "style.safetensors",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        )
        for spec in ("civitai:0", "civitai:abc", "civitai:1/../2", f"civitai:{VERSION} "):
            with self.subTest(spec=spec), self.assertRaisesRegex(LatentCrateError, "or civitai:<model version id>"):
                models.draft(spec, models.Tokens())

    def test_a_civitai_draft_refuses_ambiguity_and_leaves_unsafe_or_missing_fields_to_fill(self):
        self.civitai_api(
            {"files": [{"id": 8, "name": "a.safetensors", "sizeKB": 1.0}, {"id": 9, "name": "b\x1b[2J.safetensors"}]}
        )
        with self.assertRaises(LatentCrateError) as refusal:
            models.draft(f"civitai:{VERSION}", models.Tokens())
        self.assertEqual(
            str(refusal.exception),
            f"Civitai model version {VERSION} has no single primary file; name one as civitai_file: "
            r"8 'a.safetensors' (1024 bytes), 9 'b\x1b[2J.safetensors' (unknown bytes)",
        )

        self.civitai_api({"files": [{"name": 'my "lora"\n.safetensors', "sizeKB": 0.3, "hashes": {"SHA256": "X"}}]})
        text = models.draft(f"civitai:{VERSION}", models.Tokens())
        entry = tomllib.loads(text)["file"][0]
        self.assertEqual((entry["destination"], entry["size"], entry["sha256"]), ("TODO", "TODO", "TODO"))
        self.assertNotIn("civitai_file", text)

    def test_remote_check_compares_a_civitai_version_with_its_record(self):
        model_set, model_file = self.civitai_set()
        other = models.ModelFile(**{**model_file.__dict__, "destination": "loras/copy.safetensors", "sha256": "f" * 64})
        model_set = models.ModelSet(**{**model_set.__dict__, "files": (model_file, other)})
        record = {"primary": True, "sizeKB": model_file.size / 1024, "hashes": {"SHA256": model_file.sha256.upper()}}
        requests = self.civitai_api({"files": [record]})
        links = self.serve_links()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(models.check([model_set], models.Tokens(TOKEN, CIVITAI_TOKEN), remote=True), 1)
        self.assertEqual(
            output.getvalue().splitlines()[1:],
            [
                f"civitai:{VERSION} loras/style.safetensors: ok",
                f"civitai:{VERSION} loras/copy.safetensors: remote sha256 {model_file.sha256}, manifest {'f' * 64}",
                f"{WORKFLOW_URL}: ok",
                f"{LICENSE_URL}: HTTP 404",
            ],
        )
        self.assertEqual({token for _, _, token in links}, {None})
        self.assertEqual(requests, [(f"/api/v1/model-versions/{VERSION}", f"Bearer {CIVITAI_TOKEN}")])
        unpinned = models.ModelFile(**{**model_file.__dict__, "size": None, "sha256": None})
        self.assertIsNone(drafts.civitai_mismatch(unpinned, {"files": [{"sizeKB": 1.0}]}))
        self.assertEqual(
            drafts.civitai_mismatch(model_file, {"files": [{"sizeKB": 1.0}]}),
            f"remote size 1024, manifest {model_file.size}",
        )
        chosen = models.ModelFile(**{**model_file.__dict__, "civitai_file": 8})
        self.assertIsNone(drafts.civitai_mismatch(chosen, {"files": [{"id": 9, "primary": True}, {**record, "id": 8}]}))
        self.assertEqual(
            drafts.civitai_mismatch(chosen, {"files": [{**record, "id": 9}, {"id": 8, "sizeKB": 1.0}]}),
            f"remote size 1024, manifest {model_file.size}",
        )

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
        same_size = {model_file.source: {**remote, "size": model_file.size}}
        self.assertEqual(
            drafts.remote_mismatch(model_file, same_size), f"remote sha256 {'f' * 64}, manifest {model_file.sha256}"
        )

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
            self.assertEqual(models.check([model_set], models.Tokens(TOKEN), remote=False), 0)
        self.assertEqual(output.getvalue(), "fixture: 3 file(s), 1 license(s)\n")

        requests: list[tuple[str, str, str | None]] = []

        def respond(handler):
            requests.append((handler.command, handler.path, handler.headers.get("Authorization")))
            serve_json(
                handler,
                {
                    "siblings": [
                        {"rfilename": model_file.source, "size": model_file.size, "lfs": {"sha256": model_file.sha256}},
                        {"rfilename": other.source, "size": other.size + 1, "lfs": {"sha256": other.sha256}},
                        {"rfilename": plain.source, "size": plain.size},
                    ]
                },
            )

        self.use_hub(self.serve(respond))
        links = self.serve_links()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(models.check([model_set], models.Tokens(TOKEN), remote=True), 1)

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
            requests, [("GET", f"/api/models/example/models/revision/{COMMIT}?blobs=true", f"Bearer {TOKEN}")]
        )
        # A GitHub blob is checked as its raw file; neither link is a provider's origin, so neither gets a token.
        self.assertEqual(
            links,
            [
                ("HEAD", f"/Comfy-Org/workflow_templates/{COMMIT}/templates/fixture.json", None),
                ("HEAD", f"/example/model/blob/{COMMIT}/LICENSE", None),
            ],
        )


if __name__ == "__main__":
    unittest.main()
