"""Unit tests for the pin checker (resolvers with mocked sources and the report text)."""

from __future__ import annotations

import contextlib
import io
import re
import unittest
import urllib.error
import urllib.request
from unittest import mock

from latentcrate import LatentCrateError, checkpins

# A pins.env as `env.load_pins` returns it: every pin in file order.
PINS = {
    "PYTORCH_DEVEL_TAG": "2.13.0-cuda13.0-cudnn9-devel",
    "PYTORCH_DEVEL_DIGEST": "a" * 64,
    "PYTORCH_RUNTIME_TAG": "2.13.0-cuda13.0-cudnn9-runtime",
    "PYTORCH_RUNTIME_DIGEST": "b" * 64,
    "TORCHCODEC_VERSION": "0.16.0+cu130",
    "CUDA_NPP_VERSION": "13.0.1.2",
    "COMFYUI_REF": "v0.34.0",
    "COMFYUI_FRONTEND_REF": "v1.53.1",
    "FFMPEG_REF": "n9.0.1",
    "NV_CODEC_HEADERS_REF": "n13.1.15.0",
    "SVT_AV1_REF": "v4.2.0",
    "SAGEATTENTION_REF": "v2.2.0",
    "FRONTEND_NODE_TAG": "26-bookworm-slim",
    "FRONTEND_NODE_DIGEST": "c" * 64,
    "FRONTEND_PNPM_VERSION": "11.24.0",
    "CUDA_MIN_DRIVER_MAJOR": "580",
}
COMMIT = "0123456789abcdef" * 2 + "01234567"


def current(key: str, value: str) -> checkpins.Resolution:
    return checkpins.Resolution(key, value, value)


def resolve(key: str, values: dict[str, str]) -> checkpins.Resolution:
    return checkpins.RESOLVERS[key](values)


def tagged(*names: str) -> list[dict[str, str]]:
    return [{"name": name} for name in names]


class NoNetworkTests(unittest.TestCase):
    """Every test below mocks the source it needs; a resolver that reaches
    urlopen anyway fails here instead of on the network."""

    def setUp(self) -> None:
        patcher = mock.patch.object(checkpins.OPENER, "open", side_effect=AssertionError("network request"))
        patcher.start()
        self.addCleanup(patcher.stop)
        checkpins.pytorch_tags.cache_clear()
        self.addCleanup(checkpins.pytorch_tags.cache_clear)


class PyTorchTests(NoNetworkTests):
    def test_both_pins_follow_the_newest_release_with_a_devel_and_runtime_pair(self) -> None:
        # 2.15.0 has a devel tag only and 2.14.1 a runtime tag only, so
        # 2.14.0 is the newest release both pins can move to; a tag of
        # another family in the listing is ignored.
        tags = [
            "2.15.0-cuda13.0-cudnn9-devel",
            "2.14.1-cuda13.0-cudnn9-runtime",
            "2.14.0-cuda13.0-cudnn9-devel",
            "2.14.0-cuda13.0-cudnn9-runtime",
            "2.16.0-cuda13.1-cudnn9-devel",
            "2.16.0-cuda13.1-cudnn9-runtime",
            "2.13.0-cuda13.0-cudnn9-devel",
            "2.13.0-cuda13.0-cudnn9-runtime",
        ]
        published = {tag: f"sha256:{index:064x}" for index, tag in enumerate(tags)}
        with mock.patch.object(checkpins, "docker_hub_tags", return_value=published) as listed:
            devel = resolve("PYTORCH_DEVEL_TAG", PINS)
            runtime = resolve("PYTORCH_RUNTIME_TAG", PINS)
        # One listing of the pinned family serves the two rows.
        listed.assert_called_once()
        self.assertEqual(listed.call_args.args[:3], ("pytorch", "pytorch", "cuda13.0-cudnn9"))
        self.assertEqual((devel.key, devel.latest, devel.digest), ("PYTORCH_DEVEL_TAG", tags[2], f"{2:064x}"))
        self.assertEqual((runtime.key, runtime.latest, runtime.digest), ("PYTORCH_RUNTIME_TAG", tags[3], f"{3:064x}"))
        self.assertEqual(devel.notes, ("  Image tags: https://hub.docker.com/r/pytorch/pytorch/tags",))
        self.assertEqual(runtime.notes, devel.notes)

    def test_the_pinned_release_is_current_when_no_newer_pair_exists(self) -> None:
        published = dict.fromkeys(
            ["2.14.0-cuda13.0-cudnn9-devel", "2.13.0-cuda13.0-cudnn9-devel", "2.13.0-cuda13.0-cudnn9-runtime"],
            "sha256:" + "0" * 64,
        )
        with mock.patch.object(checkpins, "docker_hub_tags", return_value=published):
            self.assertEqual(
                resolve("PYTORCH_DEVEL_TAG", PINS), current("PYTORCH_DEVEL_TAG", PINS["PYTORCH_DEVEL_TAG"])
            )
        checkpins.pytorch_tags.cache_clear()
        older = dict.fromkeys(["2.12.0-cuda13.0-cudnn9-devel", "2.12.0-cuda13.0-cudnn9-runtime"], "sha256:" + "0" * 64)
        with mock.patch.object(checkpins, "docker_hub_tags", return_value=older):
            with self.assertRaisesRegex(LatentCrateError, "older than the current pin"):
                resolve("PYTORCH_RUNTIME_TAG", PINS)
        checkpins.pytorch_tags.cache_clear()
        with mock.patch.object(checkpins, "docker_hub_tags", return_value={"2.14.0-cuda13.0-cudnn9-devel": ""}):
            with self.assertRaisesRegex(LatentCrateError, "no devel and runtime tag pair of the cuda13.0-cudnn9"):
                resolve("PYTORCH_RUNTIME_TAG", PINS)

    def test_a_tag_outside_the_grammar_or_without_a_digest_is_refused(self) -> None:
        with self.assertRaisesRegex(LatentCrateError, "unsupported current PYTORCH_DEVEL_TAG: 2.13.0-cuda13.0-devel"):
            resolve("PYTORCH_DEVEL_TAG", {"PYTORCH_DEVEL_TAG": "2.13.0-cuda13.0-devel"})
        with self.assertRaisesRegex(LatentCrateError, "pins.env has no value for PYTORCH_RUNTIME_TAG"):
            resolve("PYTORCH_RUNTIME_TAG", {})
        published = dict.fromkeys(["2.14.0-cuda13.0-cudnn9-devel", "2.14.0-cuda13.0-cudnn9-runtime"], "")
        with mock.patch.object(checkpins, "docker_hub_tags", return_value=published):
            with self.assertRaisesRegex(LatentCrateError, "no sha256 manifest digest"):
                resolve("PYTORCH_DEVEL_TAG", PINS)


class GitRefTests(NoNetworkTests):
    def test_github_releases_resolve_the_latest_release_tag(self) -> None:
        with mock.patch.object(checkpins, "request_json", return_value={"tag_name": "v0.35.0"}) as request:
            result = resolve("COMFYUI_REF", PINS)
        self.assertEqual(request.call_args.args[0], "https://api.github.com/repos/Comfy-Org/ComfyUI/releases/latest")
        self.assertEqual(request.call_args.kwargs["accept"], "application/vnd.github+json")
        self.assertEqual((result.key, result.latest, result.digest), ("COMFYUI_REF", "v0.35.0", ""))
        self.assertEqual(result.notes, ("  Release history: https://github.com/Comfy-Org/ComfyUI/releases",))
        with mock.patch.object(checkpins, "request_json", return_value={"tag_name": "v1.53.1"}) as request:
            self.assertEqual(resolve("COMFYUI_FRONTEND_REF", PINS), current("COMFYUI_FRONTEND_REF", "v1.53.1"))
        self.assertIn("/repos/Comfy-Org/ComfyUI_frontend/releases/latest", request.call_args.args[0])

    def test_github_releases_refuse_an_older_prerelease_or_missing_latest(self) -> None:
        with mock.patch.object(checkpins, "request_json", return_value={"tag_name": "v0.33.0"}):
            with self.assertRaisesRegex(LatentCrateError, "GitHub Comfy-Org/ComfyUI latest eligible version is older"):
                resolve("COMFYUI_REF", PINS)
        with mock.patch.object(checkpins, "request_json", return_value={"tag_name": "v1.54.0-beta.1"}):
            with self.assertRaisesRegex(LatentCrateError, "returned no stable version tags"):
                resolve("COMFYUI_FRONTEND_REF", PINS)
        with mock.patch.object(checkpins, "request_json", return_value={"message": "Not Found"}):
            with self.assertRaisesRegex(LatentCrateError, "GitHub Comfy-Org/ComfyUI has no latest release"):
                resolve("COMFYUI_REF", PINS)

    def test_github_tags_resolve_the_newest_stable_tag(self) -> None:
        listing = tagged("n9.1-dev", "n9.1", "n9.0.2", "n9.0.1") + ["junk", {"name": ""}]
        with mock.patch.object(checkpins, "request_json", return_value=listing) as request:
            result = resolve("FFMPEG_REF", PINS)
        self.assertEqual(request.call_args.args[0], "https://api.github.com/repos/FFmpeg/FFmpeg/tags?per_page=100")
        self.assertEqual((result.key, result.latest), ("FFMPEG_REF", "n9.1"))
        self.assertEqual(result.notes, ("  Release history: https://github.com/FFmpeg/FFmpeg/tags",))
        with mock.patch.object(checkpins, "request_json", return_value=tagged("n13.1.15.0", "n13.0.19.0")) as request:
            self.assertEqual(resolve("NV_CODEC_HEADERS_REF", PINS), current("NV_CODEC_HEADERS_REF", "n13.1.15.0"))
        self.assertIn("/repos/FFmpeg/nv-codec-headers/tags", request.call_args.args[0])
        with mock.patch.object(checkpins, "request_json", return_value=tagged("v2.2.0", "v2.1.1")) as request:
            self.assertEqual(resolve("SAGEATTENTION_REF", PINS), current("SAGEATTENTION_REF", "v2.2.0"))
        self.assertIn("/repos/thu-ml/SageAttention/tags", request.call_args.args[0])
        with mock.patch.object(checkpins, "request_json", return_value=tagged("v2.3.0")):
            result = resolve("SAGEATTENTION_REF", PINS)
        self.assertEqual(result.notes, ("  Release history: https://github.com/thu-ml/SageAttention/tags",))

    def test_github_tags_refuse_an_older_latest_or_an_invalid_list(self) -> None:
        with mock.patch.object(checkpins, "request_json", return_value=tagged("n8.0.1", "n7.1.2")):
            with self.assertRaisesRegex(LatentCrateError, "GitHub FFmpeg/FFmpeg latest eligible version is older"):
                resolve("FFMPEG_REF", PINS)
        with mock.patch.object(checkpins, "request_json", return_value={"message": "rate limited"}):
            with self.assertRaisesRegex(LatentCrateError, "GitHub FFmpeg/FFmpeg returned an invalid tag list"):
                resolve("FFMPEG_REF", PINS)

    def test_gitlab_tags_resolve_svt_av1(self) -> None:
        with mock.patch.object(
            checkpins, "request_json", return_value=tagged("v4.3.0-rc1", "v4.3.0", "v4.2.0")
        ) as request:
            result = resolve("SVT_AV1_REF", PINS)
        self.assertEqual(
            request.call_args.args[0],
            "https://gitlab.com/api/v4/projects/AOMediaCodec%2FSVT-AV1/repository/tags?per_page=100",
        )
        self.assertEqual((result.key, result.latest), ("SVT_AV1_REF", "v4.3.0"))
        self.assertEqual(result.notes, ("  Release history: https://gitlab.com/AOMediaCodec/SVT-AV1/-/tags",))
        with mock.patch.object(checkpins, "request_json", return_value=tagged("v4.1.0")):
            with self.assertRaisesRegex(
                LatentCrateError, "GitLab AOMediaCodec/SVT-AV1 latest eligible version is older"
            ):
                resolve("SVT_AV1_REF", PINS)
        with mock.patch.object(checkpins, "request_json", return_value={"error": "x"}):
            with self.assertRaisesRegex(LatentCrateError, "GitLab AOMediaCodec/SVT-AV1 returned an invalid tag list"):
                resolve("SVT_AV1_REF", PINS)

    def test_a_ref_pinned_to_a_commit_is_unsupported_not_resolved(self) -> None:
        for key in ("COMFYUI_REF", "COMFYUI_FRONTEND_REF", "FFMPEG_REF", "NV_CODEC_HEADERS_REF", "SVT_AV1_REF"):
            with mock.patch.object(checkpins, "request_json") as request:
                with self.assertRaisesRegex(LatentCrateError, f"unsupported current {key}: {COMMIT}"):
                    resolve(key, {key: COMMIT})
            request.assert_not_called()
        with self.assertRaisesRegex(LatentCrateError, "unsupported current SAGEATTENTION_REF: main"):
            resolve("SAGEATTENTION_REF", {"SAGEATTENTION_REF": "main"})


class PackageRegistryTests(NoNetworkTests):
    def test_pnpm_uses_the_npm_latest_tag(self) -> None:
        with mock.patch.object(checkpins, "request_json", return_value={"version": "11.25.0"}) as request:
            result = resolve("FRONTEND_PNPM_VERSION", PINS)
        self.assertEqual(request.call_args.args[0], "https://registry.npmjs.org/pnpm/latest")
        self.assertEqual((result.key, result.latest, result.digest), ("FRONTEND_PNPM_VERSION", "11.25.0", ""))
        self.assertEqual(result.notes, ("  Package history: https://www.npmjs.com/package/pnpm?activeTab=versions",))
        with mock.patch.object(checkpins, "request_json", return_value={"version": "11.24.0"}):
            self.assertEqual(resolve("FRONTEND_PNPM_VERSION", PINS), current("FRONTEND_PNPM_VERSION", "11.24.0"))

    def test_pnpm_refuses_a_prerelease_older_or_unparsable_version(self) -> None:
        with mock.patch.object(checkpins, "request_json", return_value={"version": "12.0.0-beta.1"}):
            with self.assertRaisesRegex(LatentCrateError, "npm returned no stable pnpm latest version"):
                resolve("FRONTEND_PNPM_VERSION", PINS)
        with mock.patch.object(checkpins, "request_json", return_value={"version": "10.0.0"}):
            with self.assertRaisesRegex(LatentCrateError, "npm pnpm latest eligible version is older"):
                resolve("FRONTEND_PNPM_VERSION", PINS)
        with self.assertRaisesRegex(LatentCrateError, "unsupported current FRONTEND_PNPM_VERSION: latest"):
            resolve("FRONTEND_PNPM_VERSION", {"FRONTEND_PNPM_VERSION": "latest"})

    def test_torchcodec_keeps_the_pinned_cuda_suffix_on_the_pypi_latest(self) -> None:
        payload = {"info": {"version": "0.17.0"}}
        with mock.patch.object(checkpins, "request_json", return_value=payload) as request:
            result = resolve("TORCHCODEC_VERSION", PINS)
        self.assertEqual(request.call_args.args[0], "https://pypi.org/pypi/torchcodec/json")
        self.assertEqual((result.key, result.latest), ("TORCHCODEC_VERSION", "0.17.0+cu130"))
        self.assertEqual(result.notes, ("  Release history: https://github.com/meta-pytorch/torchcodec/releases",))
        with mock.patch.object(checkpins, "request_json", return_value={"info": {"version": "0.16.0"}}):
            self.assertEqual(resolve("TORCHCODEC_VERSION", PINS), current("TORCHCODEC_VERSION", "0.16.0+cu130"))

    def test_torchcodec_refuses_an_older_prerelease_or_unsuffixed_pin(self) -> None:
        with mock.patch.object(checkpins, "request_json", return_value={"info": {"version": "0.15.0"}}):
            with self.assertRaisesRegex(LatentCrateError, "PyPI torchcodec latest eligible version is older"):
                resolve("TORCHCODEC_VERSION", PINS)
        with mock.patch.object(checkpins, "request_json", return_value={"info": {"version": "0.17.0rc1"}}):
            with self.assertRaisesRegex(LatentCrateError, "PyPI returned no stable torchcodec latest version"):
                resolve("TORCHCODEC_VERSION", PINS)
        with self.assertRaisesRegex(LatentCrateError, "unsupported current TORCHCODEC_VERSION: 0.16.0"):
            resolve("TORCHCODEC_VERSION", {"TORCHCODEC_VERSION": "0.16.0"})


class DockerHubTests(NoNetworkTests):
    def test_node_keeps_the_image_family_the_tag_depth_and_even_lts_majors(self) -> None:
        tags = [
            "27-bookworm-slim",
            "28.1.0-bookworm-slim",
            "28-alpine",
            "28-bookworm",
            "28-bookworm-slim",
            "26-bookworm-slim",
        ]
        published = {tag: f"sha256:{index:064x}" for index, tag in enumerate(tags)}
        with mock.patch.object(checkpins, "docker_hub_tags", return_value=published) as listed:
            result = resolve("FRONTEND_NODE_TAG", PINS)
        self.assertEqual(listed.call_args.args[:3], ("library", "node", "bookworm-slim"))
        self.assertEqual(
            (result.key, result.latest, result.digest), ("FRONTEND_NODE_TAG", "28-bookworm-slim", f"{4:064x}")
        )
        self.assertEqual(result.notes, ("  Image tags: https://hub.docker.com/_/node",))
        with mock.patch.object(checkpins, "docker_hub_tags", return_value={"26-bookworm-slim": "sha256:" + "0" * 64}):
            self.assertEqual(resolve("FRONTEND_NODE_TAG", PINS), current("FRONTEND_NODE_TAG", "26-bookworm-slim"))

    def test_node_refuses_an_older_listing_a_missing_digest_or_another_image(self) -> None:
        with mock.patch.object(checkpins, "docker_hub_tags", return_value={"24-bookworm-slim": "sha256:" + "0" * 64}):
            with self.assertRaisesRegex(LatentCrateError, "Docker Hub library/node latest eligible version is older"):
                resolve("FRONTEND_NODE_TAG", PINS)
        with mock.patch.object(checkpins, "docker_hub_tags", return_value={"28-bookworm-slim": ""}):
            with self.assertRaisesRegex(LatentCrateError, "Docker Hub library/node returned no sha256 manifest digest"):
                resolve("FRONTEND_NODE_TAG", PINS)
        with self.assertRaisesRegex(LatentCrateError, "unsupported current FRONTEND_NODE_TAG: 26-alpine"):
            resolve("FRONTEND_NODE_TAG", {"FRONTEND_NODE_TAG": "26-alpine"})

    def test_docker_hub_is_read_newest_first_and_stops_behind_the_latest(self) -> None:
        # Three pages newest first: the latest release is on the first, the
        # second adds nothing newer, so the third is never requested.
        pages = [
            {
                "results": [
                    {"name": "latest", "digest": "sha256:l"},
                    {"name": "28-bookworm-slim", "digest": "sha256:a"},
                ],
                "next": "https://hub.docker.com/v2/x?page=2",
            },
            {
                "results": [{"name": "26-bookworm-slim", "digest": "sha256:b"}],
                "next": "https://hub.docker.com/v2/x?page=3",
            },
            {"results": [{"name": "30-bookworm-slim", "digest": "sha256:c"}], "next": None},
        ]
        pattern = re.compile(r"^(\d+(?:\.\d+)*)-bookworm-slim$")
        with mock.patch.object(checkpins, "request_json", side_effect=pages) as request:
            tags = checkpins.docker_hub_tags(
                "library", "node", "bookworm-slim", lambda tag: checkpins.version_key(tag, pattern)
            )
        self.assertEqual(request.call_count, 2)
        self.assertIn("ordering=last_updated", request.call_args_list[0].args[0])
        self.assertEqual(tags, {"latest": "sha256:l", "28-bookworm-slim": "sha256:a", "26-bookworm-slim": "sha256:b"})

    def test_docker_hub_reads_on_while_a_page_continues_the_newest_release(self) -> None:
        # The runtime tag of 2.15.0 ends the first page and its devel tag
        # opens the second; the third adds only older tags, so the fourth
        # is never requested.
        pages = [
            {
                "results": [{"name": "2.15.0-cuda13.0-cudnn9-runtime", "digest": "sha256:" + "a" * 64}],
                "next": "https://hub.docker.com/v2/x?page=2",
            },
            {
                "results": [
                    {"name": "2.15.0-cuda13.0-cudnn9-devel", "digest": "sha256:" + "d" * 64},
                    {"name": "2.14.0-cuda13.0-cudnn9-devel", "digest": "sha256:" + "b" * 64},
                ],
                "next": "https://hub.docker.com/v2/x?page=3",
            },
            {
                "results": [{"name": "2.14.0-cuda13.0-cudnn9-runtime", "digest": "sha256:" + "c" * 64}],
                "next": "https://hub.docker.com/v2/x?page=4",
            },
            {"results": [{"name": "2.13.0-cuda13.0-cudnn9-devel", "digest": "sha256:" + "e" * 64}], "next": None},
        ]
        with mock.patch.object(checkpins, "request_json", side_effect=pages) as request:
            devel = resolve("PYTORCH_DEVEL_TAG", PINS)
        self.assertEqual(request.call_count, 3)
        self.assertEqual((devel.latest, devel.digest), ("2.15.0-cuda13.0-cudnn9-devel", "d" * 64))

    def test_a_newest_release_with_one_tag_falls_back_to_the_newest_complete_pair(self) -> None:
        pages = [
            {
                "results": [{"name": "2.15.0-cuda13.0-cudnn9-runtime", "digest": "sha256:a"}],
                "next": "https://hub.docker.com/v2/x?page=2",
            },
            {
                "results": [
                    {"name": "2.14.0-cuda13.0-cudnn9-devel", "digest": "sha256:" + "b" * 64},
                    {"name": "2.14.0-cuda13.0-cudnn9-runtime", "digest": "sha256:" + "c" * 64},
                ],
                "next": "https://hub.docker.com/v2/x?page=3",
            },
        ] + [{"results": [], "next": "https://hub.docker.com/v2/x?page=99"}] * checkpins.DOCKER_HUB_PAGE_LIMIT
        with mock.patch.object(checkpins, "request_json", side_effect=pages) as request:
            devel = resolve("PYTORCH_DEVEL_TAG", PINS)
            runtime = resolve("PYTORCH_RUNTIME_TAG", PINS)
        self.assertEqual(request.call_count, 2)
        self.assertEqual((devel.latest, devel.digest), ("2.14.0-cuda13.0-cudnn9-devel", "b" * 64))
        self.assertEqual((runtime.latest, runtime.digest), ("2.14.0-cuda13.0-cudnn9-runtime", "c" * 64))

    def test_docker_hub_stops_at_the_anonymous_page_limit(self) -> None:
        page = {"results": [{"name": "x", "digest": ""}], "next": "https://hub.docker.com/v2/x?page=2"}
        with (
            mock.patch.object(checkpins, "request_json", return_value=page) as request,
            self.assertRaisesRegex(
                LatentCrateError, "Docker Hub tags for library/node exceeded the 10-page listing limit"
            ),
        ):
            checkpins.docker_hub_tags("library", "node", "bookworm-slim")
        self.assertEqual(request.call_count, checkpins.DOCKER_HUB_PAGE_LIMIT)

    def test_docker_hub_rejects_an_unsafe_pagination_link_and_an_invalid_listing(self) -> None:
        payload = {"results": [], "next": "http://hub.docker.com/next"}
        with mock.patch.object(checkpins, "request_json", return_value=payload):
            with self.assertRaisesRegex(LatentCrateError, "unsafe pagination"):
                checkpins.docker_hub_tags("library", "node", "bookworm-slim")
        with mock.patch.object(checkpins, "request_json", return_value={"results": "x"}):
            with self.assertRaisesRegex(LatentCrateError, "Docker Hub returned invalid tags for pytorch/pytorch"):
                checkpins.docker_hub_tags("pytorch", "pytorch", "cuda13.0-cudnn9")


class RequestTests(unittest.TestCase):
    def test_request_rejects_non_https_url_before_opening_it(self) -> None:
        with (
            mock.patch.object(checkpins.OPENER, "open") as opened,
            self.assertRaisesRegex(LatentCrateError, "must use HTTPS"),
        ):
            checkpins.request_bytes("http://registry.example/pkg")
        opened.assert_not_called()

    def test_a_redirect_is_followed_to_https_and_refused_elsewhere(self) -> None:
        self.assertTrue(any(isinstance(handler, checkpins.HttpsRedirects) for handler in checkpins.OPENER.handlers))
        handler = checkpins.HttpsRedirects()
        request = urllib.request.Request("https://registry.example/pkg", headers={"Accept": "application/json"})
        followed = handler.redirect_request(request, None, 302, "Found", {}, "https://cdn.example/pkg")
        self.assertEqual(followed.full_url, "https://cdn.example/pkg")
        with self.assertRaisesRegex(
            LatentCrateError, "^pin source redirected away from HTTPS: http://cdn.example/pkg$"
        ):
            handler.redirect_request(request, None, 302, "Found", {}, "http://cdn.example/pkg")

    def test_request_refuses_a_body_over_the_cap(self) -> None:
        def served(body: bytes) -> mock._patch:
            response = mock.Mock(read=lambda size: body[:size])
            return mock.patch.object(checkpins.OPENER, "open", return_value=contextlib.nullcontext(response))

        # The body is read one byte past the cap, so a longer one is refused
        # without trusting a Content-Length header.
        too_long = b"x" * (checkpins.MAX_RESPONSE_BYTES + 1)
        with served(too_long), self.assertRaisesRegex(LatentCrateError, "too large"):
            checkpins.request_bytes("https://registry.example/pkg")
        with served(too_long[1:]):
            self.assertEqual(len(checkpins.request_bytes("https://registry.example/pkg")), checkpins.MAX_RESPONSE_BYTES)
        with served(b"{not json"), self.assertRaisesRegex(LatentCrateError, "invalid JSON"):
            checkpins.request_json("https://registry.example/pkg")

    def test_a_refusal_names_the_status_and_the_rate_limit_headers(self) -> None:
        bodies = []

        def refused(request, timeout):
            bodies.append(io.BytesIO())
            headers = {"X-RateLimit-Remaining": "0", "Retry-After": "42"}
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", headers, bodies[-1])

        with mock.patch.object(checkpins.OPENER, "open", side_effect=refused):
            with self.assertRaisesRegex(LatentCrateError, "HTTP 403, rate limit remaining 0, retry after 42 seconds$"):
                checkpins.request_bytes("https://api.github.com/repos/x/releases")
        # The refused response is closed, not left to the GC.
        self.assertEqual([body.closed for body in bodies], [True])
        self.assertEqual(checkpins.USER_AGENT, "LatentCrate-pins-check")


class ReportTests(NoNetworkTests):
    def test_the_component_names_are_derived_from_the_pin_keys(self) -> None:
        names = [checkpins.component_name(key) for key in PINS if not key.endswith("_DIGEST")]
        self.assertEqual(
            names,
            [
                "pytorch-devel",
                "pytorch-runtime",
                "torchcodec",
                "cuda-npp",
                "comfyui",
                "comfyui-frontend",
                "ffmpeg",
                "nv-codec-headers",
                "svt-av1",
                "sageattention",
                "frontend-node",
                "frontend-pnpm",
                "cuda-min-driver-major",
            ],
        )
        # Every pin but the driver major has a resolver, and no resolver is
        # keyed by a pin the shipped file lacks.
        pins = {key for key in PINS if not key.endswith("_DIGEST")}
        self.assertEqual(set(checkpins.RESOLVERS), pins - {"CUDA_NPP_VERSION", "CUDA_MIN_DRIVER_MAJOR"})

    def test_reports_every_pin_and_a_paste_block_for_the_newer_ones(self) -> None:
        resolutions = {
            "PYTORCH_DEVEL_TAG": checkpins.Resolution(
                "PYTORCH_DEVEL_TAG",
                "2.13.0-cuda13.0-cudnn9-devel",
                "2.14.0-cuda13.0-cudnn9-devel",
                ("  Image tags: https://example/pytorch",),
                "1" * 64,
            ),
            "PYTORCH_RUNTIME_TAG": checkpins.Resolution(
                "PYTORCH_RUNTIME_TAG",
                "2.13.0-cuda13.0-cudnn9-runtime",
                "2.14.0-cuda13.0-cudnn9-runtime",
                ("  Image tags: https://example/pytorch",),
                "2" * 64,
            ),
            "TORCHCODEC_VERSION": checkpins.Resolution(
                "TORCHCODEC_VERSION", "0.16.0+cu130", "0.17.0+cu130", ("  Release history: https://example/torchcodec",)
            ),
            "COMFYUI_REF": current("COMFYUI_REF", "v0.34.0"),
            "COMFYUI_FRONTEND_REF": current("COMFYUI_FRONTEND_REF", "v1.53.1"),
            "FFMPEG_REF": current("FFMPEG_REF", "n9.0.1"),
            "NV_CODEC_HEADERS_REF": current("NV_CODEC_HEADERS_REF", "n13.1.15.0"),
            "SVT_AV1_REF": current("SVT_AV1_REF", "v4.2.0"),
            "SAGEATTENTION_REF": current("SAGEATTENTION_REF", "v2.2.0"),
            "FRONTEND_NODE_TAG": current("FRONTEND_NODE_TAG", "26-bookworm-slim"),
            "FRONTEND_PNPM_VERSION": current("FRONTEND_PNPM_VERSION", "11.24.0"),
        }
        resolvers = {key: mock.Mock(return_value=resolution) for key, resolution in resolutions.items()}
        with (
            mock.patch.dict(checkpins.RESOLVERS, resolvers, clear=True),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            output = checkpins.check("all", PINS)

        self.assertEqual(
            output,
            "pytorch-devel: 2.13.0-cuda13.0-cudnn9-devel -> 2.14.0-cuda13.0-cudnn9-devel\n"
            "  Image tags: https://example/pytorch\n"
            "pytorch-runtime: 2.13.0-cuda13.0-cudnn9-runtime -> 2.14.0-cuda13.0-cudnn9-runtime\n"
            "  Image tags: https://example/pytorch\n"
            "torchcodec: 0.16.0+cu130 -> 0.17.0+cu130\n"
            "  Release history: https://example/torchcodec\n"
            "cuda-npp: 13.0.1.2 (no resolver; look it up by hand)\n"
            "comfyui: v0.34.0 (latest eligible)\n"
            "comfyui-frontend: v1.53.1 (latest eligible)\n"
            "ffmpeg: n9.0.1 (latest eligible)\n"
            "nv-codec-headers: n13.1.15.0 (latest eligible)\n"
            "svt-av1: v4.2.0 (latest eligible)\n"
            "sageattention: v2.2.0 (latest eligible)\n"
            "frontend-node: 26-bookworm-slim (latest eligible)\n"
            "frontend-pnpm: 11.24.0 (latest eligible)\n"
            # The pins without a resolver are rows too, so the coverage of
            # the check is its output.
            "cuda-min-driver-major: 580 (no resolver; look it up by hand)\n"
            "\n"
            "Paste into pins.env:\n"
            # The digest belongs to the tag above it; a version pin has none.
            "PYTORCH_DEVEL_TAG=2.14.0-cuda13.0-cudnn9-devel\n"
            f"PYTORCH_DEVEL_DIGEST={'1' * 64}\n"
            "PYTORCH_RUNTIME_TAG=2.14.0-cuda13.0-cudnn9-runtime\n"
            f"PYTORCH_RUNTIME_DIGEST={'2' * 64}\n"
            "TORCHCODEC_VERSION=0.17.0+cu130\n",
        )
        for resolver in resolvers.values():
            resolver.assert_called_once_with(PINS)
        # The report is returned, not printed; the CLI prints it.
        self.assertEqual(stdout.getvalue(), "")

    def test_a_pin_without_a_resolver_can_be_selected_and_qualifies_the_summary(self) -> None:
        self.assertEqual(
            checkpins.check("cuda-min-driver-major", PINS),
            "cuda-min-driver-major: 580 (no resolver; look it up by hand)\n",
        )
        resolvers = {"FRONTEND_PNPM_VERSION": mock.Mock(return_value=current("FRONTEND_PNPM_VERSION", "11.24.0"))}
        with mock.patch.dict(checkpins.RESOLVERS, resolvers, clear=True):
            output = checkpins.check("all", {"FRONTEND_PNPM_VERSION": "11.24.0", "CUDA_MIN_DRIVER_MAJOR": "580"})
        self.assertEqual(
            output,
            "frontend-pnpm: 11.24.0 (latest eligible)\n"
            "cuda-min-driver-major: 580 (no resolver; look it up by hand)\n"
            "The resolved pins are the latest eligible.\n",
        )

    def test_reports_latest_eligible_pins_without_a_paste_block(self) -> None:
        resolver = mock.Mock(return_value=current("FRONTEND_NODE_TAG", "26-bookworm-slim"))
        with mock.patch.dict(checkpins.RESOLVERS, {"FRONTEND_NODE_TAG": resolver}, clear=True):
            output = checkpins.check("frontend-node", PINS)
        resolver.assert_called_once_with(PINS)
        self.assertEqual(
            output, "frontend-node: 26-bookworm-slim (latest eligible)\npins.env has the latest eligible pins.\n"
        )

    def test_a_failing_source_is_one_row_and_the_report_is_raised(self) -> None:
        # The other pins are still reported, and the exception carries the
        # report so the command can print it and still exit with a failure.
        resolvers = {
            "FRONTEND_PNPM_VERSION": mock.Mock(
                return_value=checkpins.Resolution("FRONTEND_PNPM_VERSION", "11.24.0", "11.25.0")
            ),
            "FRONTEND_NODE_TAG": mock.Mock(side_effect=LatentCrateError("later source failed")),
        }
        with (
            mock.patch.dict(checkpins.RESOLVERS, resolvers, clear=True),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            self.assertRaises(checkpins.LookupsFailed) as raised,
        ):
            checkpins.check("all", PINS)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(raised.exception.failed, ["frontend-node"])
        self.assertIn("pins check could not read the source of: frontend-node", str(raised.exception))
        report = raised.exception.report
        self.assertIn("frontend-node: 26-bookworm-slim (lookup failed: later source failed)\n", report)
        self.assertIn("frontend-pnpm: 11.24.0 -> 11.25.0\n", report)
        self.assertIn("cuda-min-driver-major: 580 (no resolver; look it up by hand)\n", report)
        self.assertIn("Paste into pins.env:\nFRONTEND_PNPM_VERSION=11.25.0\n", report)

    def test_rejects_an_unknown_component_before_resolving(self) -> None:
        resolver = mock.Mock()
        with (
            mock.patch.dict(checkpins.RESOLVERS, {"FRONTEND_NODE_TAG": resolver}, clear=True),
            self.assertRaisesRegex(
                LatentCrateError,
                "unknown pin component 'pytorch'; choose from: pytorch-devel, pytorch-runtime, torchcodec, cuda-npp, "
                "comfyui, comfyui-frontend, ffmpeg, nv-codec-headers, svt-av1, sageattention, frontend-node, "
                "frontend-pnpm, cuda-min-driver-major, all",
            ),
        ):
            checkpins.check("pytorch", PINS)
        resolver.assert_not_called()
        # A digest is resolved with its tag, never selected on its own.
        with self.assertRaisesRegex(LatentCrateError, "unknown pin component 'frontend-node-digest'"):
            checkpins.check("frontend-node-digest", PINS)


if __name__ == "__main__":
    unittest.main()
