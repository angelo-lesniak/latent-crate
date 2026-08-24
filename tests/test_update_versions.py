#!/usr/bin/env python3
"""Unit tests for the containerized version resolver."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "services" / "tools" / "update-versions.py"
SPEC = importlib.util.spec_from_file_location("update_versions", SCRIPT)
assert SPEC and SPEC.loader
update_versions = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = update_versions
SPEC.loader.exec_module(update_versions)


class VersionResolverTests(unittest.TestCase):
    def test_comfyui_uses_latest_stable_tag_and_lists_intervening_releases(self) -> None:
        values = {"COMFYUI_REF": "v1.2.0"}

        def response(url: str, **_kwargs: object) -> object:
            if "/tags?" in url:
                return [{"name": "v1.3.0-rc1"}, {"name": "v1.4.0"}, {"name": "v1.3.0"}]
            return [
                {"tag_name": "v1.4.0", "name": "Four", "html_url": "https://example/four"},
                {"tag_name": "v1.3.0", "name": "Three", "html_url": "https://example/three"},
                {"tag_name": "v1.5.0", "name": "Future", "html_url": "https://example/future"},
            ]

        with mock.patch.object(update_versions, "request_json", side_effect=response):
            result = update_versions.resolve_component("comfyui", values)

        self.assertEqual(result.updates, (("COMFYUI_REF", "v1.4.0"),))
        self.assertEqual(
            result.notes,
            ("  Three: https://example/three", "  Four: https://example/four"),
        )

    def test_github_tags_and_release_notes_follow_pagination(self) -> None:
        source = update_versions.GITHUB_SOURCES["comfyui"]

        def response(url: str, **_kwargs: object) -> object:
            page_one = "&page=1" in url
            if "/tags?" in url:
                return ([{"name": "v1.0.0-rc1"}] * 100) if page_one else [{"name": "v1.2.0"}]
            return ([{"draft": True}] * 100) if page_one else [
                {
                    "tag_name": "v1.2.0",
                    "name": "Second page",
                    "html_url": "https://example/second-page",
                }
            ]

        with mock.patch.object(update_versions, "request_json", side_effect=response):
            tags = update_versions.github_tags(source)
            notes = update_versions.github_release_notes(source, "v1.0.0", "v1.2.0")

        self.assertIn("v1.2.0", tags)
        self.assertEqual(notes, ("  Second page: https://example/second-page",))

    def test_frontend_updates_reference_and_archive_digest_together(self) -> None:
        values = {"COMFYUI_FRONTEND_REF": "Comfy-Org/ComfyUI_frontend@v1.2.0"}
        with (
            mock.patch.object(update_versions, "github_tags", return_value=["v1.3.0"]),
            mock.patch.object(update_versions, "github_release_notes", return_value=()),
            mock.patch.object(update_versions, "frontend_digest", return_value="a" * 64),
        ):
            result = update_versions.resolve_component("frontend", values)

        self.assertEqual(
            result.updates,
            (
                ("COMFYUI_FRONTEND_REF", "Comfy-Org/ComfyUI_frontend@v1.3.0"),
                ("COMFY_FRONTEND_DIST_SHA256", "a" * 64),
            ),
        )

    def test_frontend_populates_empty_digest_when_tag_is_already_latest(self) -> None:
        reference = "Comfy-Org/ComfyUI_frontend@v1.3.0"
        digest = "a" * 64
        repaired = (
            ("COMFYUI_FRONTEND_REF", reference),
            ("COMFY_FRONTEND_DIST_SHA256", digest),
        )
        for current_digest, expected_updates in (
            ("", repaired),
            (digest, ()),
        ):
            with (
                self.subTest(current_digest=current_digest),
                mock.patch.object(update_versions, "github_tags", return_value=["v1.3.0"]),
                mock.patch.object(update_versions, "frontend_digest", return_value=digest) as pin,
            ):
                result = update_versions.resolve_component(
                    "frontend",
                    {
                        "COMFYUI_FRONTEND_REF": reference,
                        "COMFY_FRONTEND_DIST_SHA256": current_digest,
                    },
                )
            self.assertEqual(result.updates, expected_updates)
            pin.assert_called_once_with(reference)

    def test_frontend_changed_same_tag_digest_fails_without_protocol_output(self) -> None:
        reference = "Comfy-Org/ComfyUI_frontend@v1.3.0"
        with (
            mock.patch.object(update_versions, "github_tags", return_value=["v1.3.0"]),
            mock.patch.object(update_versions, "frontend_digest", return_value="a" * 64),
            mock.patch.object(
                sys,
                "argv",
                [
                    "update-versions.py",
                    "resolve",
                    "frontend",
                    f"COMFYUI_FRONTEND_REF={reference}",
                    f"COMFY_FRONTEND_DIST_SHA256={'b' * 64}",
                ],
            ),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            self.assertRaises(SystemExit) as raised,
        ):
            update_versions.main()
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("digest changed for the unchanged release", str(raised.exception))

    def test_github_fails_when_latest_eligible_tag_is_older_than_pin(self) -> None:
        with mock.patch.object(update_versions, "github_tags", return_value=["v1.0.0"]):
            with self.assertRaisesRegex(update_versions.VersionUpdateError, "older than"):
                update_versions.resolve_component("comfyui", {"COMFYUI_REF": "v99.0.0"})

    def test_svt_uses_latest_stable_gitlab_release(self) -> None:
        releases = [
            {"tag_name": "v3.1.0", "name": "3.1"},
            {"tag_name": "v3.2.0-rc1", "name": "candidate"},
            {"tag_name": "v3.2.0", "name": "3.2"},
            {"tag_name": "v4.0.0", "name": "future", "upcoming_release": True},
        ]
        with mock.patch.object(update_versions, "request_json", return_value=releases):
            result = update_versions.resolve_component("svt-av1", {"SVT_AV1_REF": "v3.1.0"})
        self.assertEqual(result.updates, (("SVT_AV1_REF", "v3.2.0"),))
        self.assertIn("3.2", result.notes[0])

    def test_svt_follows_gitlab_pagination(self) -> None:
        def response(url: str, **_kwargs: object) -> object:
            if "&page=1" in url:
                return [{"tag_name": "v3.1.0", "upcoming_release": True}] * 100
            return [{"tag_name": "v3.2.0", "name": "Second page"}]

        with mock.patch.object(update_versions, "request_json", side_effect=response):
            result = update_versions.resolve_component("svt-av1", {"SVT_AV1_REF": "v3.1.0"})

        self.assertEqual(result.updates, (("SVT_AV1_REF", "v3.2.0"),))

    def test_pnpm_uses_npm_latest(self) -> None:
        with mock.patch.object(update_versions, "request_json", return_value={"version": "11.2.0"}):
            result = update_versions.resolve_component("pnpm", {"FRONTEND_PNPM_VERSION": "11.1.0"})
        self.assertEqual(result.updates, (("FRONTEND_PNPM_VERSION", "11.2.0"),))

    def test_torchcodec_preserves_index_build_suffix(self) -> None:
        payload = b"""<!doctype html>
        <a href="torchcodec-0.8.0%2Bcu130-cp313.whl">old</a>
        <a href="torchcodec-0.9.0%2Bcu128-cp313.whl">other index</a>
        <a href="torchcodec-0.9.0%2Bcu130-cp313.whl">latest</a>"""
        values = {
            "TORCHCODEC_VERSION": "0.8.0+cu130",
            "TORCHCODEC_INDEX_URL": "https://download.pytorch.org/whl/cu130",
        }
        with mock.patch.object(update_versions, "request_bytes", return_value=payload):
            result = update_versions.resolve_component("torchcodec", values)
        self.assertEqual(result.updates, (("TORCHCODEC_VERSION", "0.9.0+cu130"),))

    def test_torchcodec_ignores_yanked_json_and_html_files(self) -> None:
        json_payload = json.dumps(
            {
                "versions": ["0.9.0+cu130", "0.99.0+cu130"],
                "files": [
                    {"filename": "torchcodec-0.9.0%2Bcu130-cp313.whl", "yanked": False},
                    {"filename": "torchcodec-0.99.0%2Bcu130-cp313.whl", "yanked": "broken"},
                ],
            }
        ).encode()
        html_payload = b"""
        <a href="torchcodec-0.9.0%2Bcu130-cp313.whl">usable</a>
        <a href="torchcodec-0.99.0%2Bcu130-cp313.whl" data-yanked>broken</a>"""

        self.assertEqual(update_versions.torchcodec_index_versions(json_payload), {"0.9.0+cu130"})
        self.assertEqual(update_versions.torchcodec_index_versions(html_payload), {"0.9.0+cu130"})

    def test_torchcodec_fails_when_yanked_current_is_newer_than_eligible_files(self) -> None:
        payload = b"""
        <a href="torchcodec-0.9.0%2Bcu130-cp313.whl">usable</a>
        <a href="torchcodec-0.99.0%2Bcu130-cp313.whl" data-yanked>broken</a>"""
        values = {
            "TORCHCODEC_VERSION": "0.99.0+cu130",
            "TORCHCODEC_INDEX_URL": "https://download.pytorch.org/whl/cu130",
        }
        with mock.patch.object(update_versions, "request_bytes", return_value=payload):
            with self.assertRaisesRegex(update_versions.VersionUpdateError, "older than"):
                update_versions.resolve_component("torchcodec", values)

    def test_clean_text_removes_terminal_and_bidirectional_controls(self) -> None:
        value = "safe\x1b]52;c;payload\x07\u202eevil\nrelease"
        self.assertEqual(update_versions.clean_text(value), "safe]52;c;payloadevil release")

    def test_empty_torchcodec_pin_remains_disabled_without_network(self) -> None:
        with mock.patch.object(update_versions, "request_bytes") as request:
            result = update_versions.resolve_component("torchcodec", {"TORCHCODEC_VERSION": ""})
        self.assertEqual(result.updates, ())
        request.assert_not_called()

    def test_node_preserves_bookworm_slim_image_family(self) -> None:
        values = {"FRONTEND_NODE_IMAGE": "docker.io/library/node:24-bookworm-slim"}
        tags = ["25-alpine", "25-bookworm", "25-bookworm-slim", "24.1-bookworm-slim"]
        with mock.patch.object(update_versions, "docker_hub_tags", return_value=tags):
            result = update_versions.resolve_component("node", values)
        self.assertEqual(
            result.updates,
            (("FRONTEND_NODE_IMAGE", "docker.io/library/node:25-bookworm-slim"),),
        )

    def test_pytorch_requires_complete_pair_in_current_cuda_family(self) -> None:
        values = {
            "PYTORCH_DEVEL_IMAGE": "docker.io/pytorch/pytorch:2.8.0-cuda13.0-cudnn9-devel",
            "PYTORCH_RUNTIME_IMAGE": "docker.io/pytorch/pytorch:2.8.0-cuda13.0-cudnn9-runtime",
        }
        tags = [
            "2.9.0-cuda13.0-cudnn9-devel",
            "2.9.0-cuda13.0-cudnn9-runtime",
            "2.10.0-cuda13.1-cudnn9-devel",
            "2.10.0-cuda13.1-cudnn9-runtime",
            "2.11.0-cuda13.0-cudnn9-devel",
        ]
        with mock.patch.object(update_versions, "docker_hub_tags", return_value=tags):
            result = update_versions.resolve_component("pytorch", values)
        self.assertEqual(
            result.updates,
            (
                ("PYTORCH_DEVEL_IMAGE", "docker.io/pytorch/pytorch:2.9.0-cuda13.0-cudnn9-devel"),
                ("PYTORCH_RUNTIME_IMAGE", "docker.io/pytorch/pytorch:2.9.0-cuda13.0-cudnn9-runtime"),
            ),
        )

    def test_all_emits_no_machine_updates_when_any_resolution_fails(self) -> None:
        first = update_versions.Resolution("comfyui", "v1", "v2", (("COMFYUI_REF", "v2"),))
        with (
            mock.patch.object(
                update_versions,
                "resolve_component",
                side_effect=[first, update_versions.VersionUpdateError("later source failed")],
            ),
            mock.patch.object(sys, "argv", ["update-versions.py", "resolve", "all", "X=1"]),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            update_versions.main()
        self.assertEqual(stdout.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
