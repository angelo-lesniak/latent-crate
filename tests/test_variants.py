"""The variant table, the Compose values it exports, Git reference
resolution through a fake git, and the source build through the fake engine."""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import yaml

from latentcrate import LatentCrateError, engine, env, variants
from tests.support import SOURCE_ROOT, Scratch

FIXTURES = SOURCE_ROOT / "tests" / "fixtures"
GIT_BIN = FIXTURES / "git-bin"
URL = "https://github.com/example/ComfyUI_frontend.git"
COMMIT = "0123456789ABCDEF0123456789abcdef01234567"
TOKEN = "fake-token-fixture"


class VariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Scratch()
        self.addCleanup(self.scratch.close)
        self.pinned = env.load_pins(self.scratch.root / "pins.env")
        self.dist = self.scratch.tmp / "dist"
        shutil.copytree(FIXTURES / "frontend-dist", self.dist)
        self.source = self.scratch.tmp / "source"
        shutil.copytree(FIXTURES / "frontend-source", self.source)

    def settings(self, **overrides: str) -> env.Settings:
        return env.load(self.scratch.root, self.scratch.environment(HF_TOKEN=TOKEN, **overrides))

    def select(self, **overrides: str) -> variants.Variant:
        return variants.select(self.settings(**overrides), self.pinned, sage=None)

    def test_mode_and_sage_name_the_target_tag_and_overlay(self) -> None:
        ref = self.pinned["COMFYUI_REF"]
        table = (
            ("release", "off", "runtime", f"release-{ref}", False),
            ("release", "available", "runtime-sage", f"release-sage-{ref}", False),
            ("git", "off", "runtime", f"git-{ref}", False),
            ("git", "global", "runtime-sage", f"git-sage-{ref}", False),
            ("dist", "off", "comfy-runtime", f"dist-{ref}", True),
            ("dist", "available", "runtime-sage", f"dist-sage-{ref}", True),
            ("source", "off", "comfy-runtime", f"dist-{ref}", True),
            ("source", "global", "runtime-sage", f"dist-sage-{ref}", True),
        )
        for mode, sage, target, tag, overlay in table:
            with self.subTest(mode=mode, sage=sage):
                settings = self.settings(
                    COMFY_FRONTEND_MODE=mode,
                    LATENTCRATE_SAGE=sage,
                    FRONTEND_GIT_URL=URL,
                    FRONTEND_GIT_REF="main",
                    COMFY_FRONTEND_DIST_DIR=str(self.dist),
                    COMFY_FRONTEND_SOURCE_DIR=str(self.source),
                )
                variant = variants.select(settings, self.pinned, sage=None)
                self.assertEqual(variant.frontend, mode)
                self.assertEqual(variant.target, target)
                self.assertEqual(variant.image(self.pinned), f"latentcrate/comfy:{tag}")
                self.assertEqual(variant.dist_overlay, overlay)
                self.assertEqual(variant.container_mode, "dist" if overlay else "git")
        # The flag wins over the setting; the repository is a setting.
        variant = variants.select(
            self.settings(LATENTCRATE_SAGE="global", LATENTCRATE_IMAGE="registry/x"), self.pinned, sage="off"
        )
        self.assertEqual((variant.sage, variant.image(self.pinned)), ("off", f"registry/x:release-{ref}"))
        with self.assertRaisesRegex(LatentCrateError, "LATENTCRATE_SAGE must be off, available, or global"):
            variants.select(self.settings(), self.pinned, sage="maybe")

    def test_compose_env_carries_every_wrapper_value_and_never_the_token(self) -> None:
        keys = {
            "LATENTCRATE_IMAGE",
            "COMFY_BUILD_TARGET",
            "COMFY_FRONTEND_MODE",
            "FRONTEND_GIT_URL",
            "FRONTEND_GIT_REF",
            "FRONTEND_GIT_REQUESTED_REF",
            "LATENTCRATE_SAGE",
        }
        release = self.select().compose_env(self.pinned)
        self.assertEqual(set(release), keys)
        self.assertEqual(release["FRONTEND_GIT_URL"], variants.RELEASE_URL)
        self.assertEqual(release["FRONTEND_GIT_REQUESTED_REF"], self.pinned["COMFYUI_FRONTEND_REF"])
        self.assertEqual(release["FRONTEND_GIT_REF"], self.pinned["COMFYUI_FRONTEND_REF"])
        self.assertEqual((release["COMFY_FRONTEND_MODE"], release["LATENTCRATE_SAGE"]), ("git", "off"))
        git = variants.select(self.settings(), self.pinned, sage="available", frontend_git=(URL, COMMIT))
        exported = git.compose_env(self.pinned)
        self.assertEqual(exported["LATENTCRATE_SAGE"], "available")
        self.assertEqual(exported["FRONTEND_GIT_REF"], COMMIT)
        git.commit = COMMIT.lower()
        self.assertEqual(git.compose_env(self.pinned)["FRONTEND_GIT_REF"], COMMIT.lower())
        dist = variants.select(self.settings(), self.pinned, sage=None, frontend_dist=str(self.dist)).compose_env(
            self.pinned
        )
        self.assertEqual(set(dist), keys | {"COMFY_FRONTEND_DIST_DIR"})
        self.assertEqual(dist["COMFY_FRONTEND_DIST_DIR"], os.path.realpath(self.dist))
        source = variants.select(self.settings(), self.pinned, sage=None, frontend_source=str(self.source))
        exported = source.compose_env(self.pinned)
        self.assertEqual(set(exported), keys | {"COMFY_FRONTEND_DIST_DIR"})
        self.assertEqual(Path(exported["COMFY_FRONTEND_DIST_DIR"]), variants.source_dist(self.settings(), self.source))
        for values in (release, dist, exported):
            self.assertNotIn(TOKEN, " ".join(values.values()))
        for values in (dist, exported):
            self.assertEqual(
                [values[key] for key in ("FRONTEND_GIT_URL", "FRONTEND_GIT_REF", "FRONTEND_GIT_REQUESTED_REF")],
                ["", "", ""],
            )
            self.assertEqual(values["COMFY_FRONTEND_MODE"], "dist")

    def test_select_refusals(self) -> None:
        link = self.dist / "assets"
        cases = (
            ({"COMFY_FRONTEND_MODE": "dist"}, "dist frontend mode requires COMFY_FRONTEND_DIST_DIR or --frontend-dist"),
            (
                {"COMFY_FRONTEND_MODE": "dist", "COMFY_FRONTEND_DIST_DIR": str(self.scratch.tmp)},
                f"frontend dist/index.html is missing or unreadable: {self.scratch.tmp}",
            ),
            (
                {"COMFY_FRONTEND_MODE": "source"},
                "frontend source mode requires COMFY_FRONTEND_SOURCE_DIR or --frontend-source",
            ),
            (
                {"COMFY_FRONTEND_MODE": "source", "COMFY_FRONTEND_SOURCE_DIR": str(self.scratch.tmp / "missing")},
                f"frontend source directory does not exist: {self.scratch.tmp / 'missing'}",
            ),
            (
                {"COMFY_FRONTEND_MODE": "source", "COMFY_FRONTEND_SOURCE_DIR": str(self.dist)},
                f"frontend source package.json is missing or unreadable: {self.dist}",
            ),
            (
                {
                    "COMFY_FRONTEND_MODE": "git",
                    "FRONTEND_GIT_URL": "https://user:pw@github.com/x/y",
                    "FRONTEND_GIT_REF": "main",
                },
                "frontend Git URL must use HTTPS; credentials in URLs are not allowed",
            ),
            (
                {
                    "COMFY_FRONTEND_MODE": "git",
                    "FRONTEND_GIT_URL": "git@github.com:x/y.git",
                    "FRONTEND_GIT_REF": "main",
                },
                "frontend Git URL must use HTTPS; credentials in URLs are not allowed",
            ),
            (
                {"COMFY_FRONTEND_MODE": "git", "FRONTEND_GIT_URL": URL},
                "frontend Git mode requires a commit, branch, tag, or pull-request reference",
            ),
            (
                {"COMFY_FRONTEND_MODE": "git", "FRONTEND_GIT_URL": URL, "FRONTEND_GIT_REF": "--upload-pack=x"},
                "frontend Git references must not begin with a dash or contain whitespace",
            ),
            (
                {"COMFY_FRONTEND_MODE": "git", "FRONTEND_GIT_URL": URL, "FRONTEND_GIT_REF": "main branch"},
                "frontend Git references must not begin with a dash or contain whitespace",
            ),
            ({"COMFY_FRONTEND_MODE": "archive"}, "unsupported COMFY_FRONTEND_MODE=archive"),
        )
        for overrides, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(LatentCrateError) as refused:
                    self.select(**overrides)
                self.assertEqual(str(refused.exception), message)
        link.symlink_to(self.scratch.tmp)
        with self.assertRaisesRegex(LatentCrateError, "frontend dist tree must not contain symbolic links"):
            variants.select(self.settings(), self.pinned, sage=None, frontend_dist=str(self.dist))
        with self.assertRaisesRegex(LatentCrateError, "choose one of --frontend-git"):
            variants.select(self.settings(), self.pinned, sage=None, frontend_dist="a", frontend_source="b")

    def resolver_env(self, mode: str, git_present: bool = True) -> dict[str, str]:
        record = self.scratch.tmp / "git-record"
        env_ = {
            "PATH": f"{GIT_BIN}:/usr/bin:/bin" if git_present else str(self.scratch.tmp / "empty"),
            "FAKE_GIT_MODE": mode,
            "FAKE_GIT_RECORD_FILE": str(record),
            "HOME": str(self.scratch.home),
        }
        record.unlink(missing_ok=True)
        return env_

    def test_resolve_lists_the_reference_through_hardened_git(self) -> None:
        git = variants.select(self.settings(), self.pinned, sage=None, frontend_git=(URL, "main"))
        for mode, expected in (("branch", "1" * 40), ("annotated-tag", "3" * 40), ("tag", "4" * 40)):
            with self.subTest(mode=mode):
                git.commit = ""
                self.assertEqual(variants.resolve(git, self.resolver_env(mode)).commit, expected)
                recorded = (self.scratch.tmp / "git-record").read_text().splitlines()
                self.assertEqual(recorded[0], "env=ok")
                self.assertEqual(
                    recorded[1:],
                    [
                        *variants.GIT_OPTIONS,
                        "ls-remote",
                        URL,
                        "main",
                        "refs/heads/main",
                        "refs/tags/main",
                        "refs/tags/main^{}",
                    ],
                )
        for mode, message in (
            ("none", "frontend reference was not found: main"),
            ("fail", f"could not query frontend reference main from {URL}"),
            ("ambiguous", "frontend reference main is ambiguous; write refs/heads/main or refs/tags/main"),
        ):
            with self.subTest(mode=mode):
                with self.assertRaises(LatentCrateError) as refused:
                    variants.resolve(git, self.resolver_env(mode))
                self.assertEqual(str(refused.exception), message)
        with self.assertRaisesRegex(LatentCrateError, "git is required to resolve a frontend branch"):
            variants.resolve(git, self.resolver_env("branch", git_present=False))
        # A full commit needs no git; the other modes carry no reference.
        passthrough = variants.select(self.settings(), self.pinned, sage=None, frontend_git=(URL, COMMIT))
        self.assertEqual(
            variants.resolve(passthrough, self.resolver_env("fail", git_present=False)).commit, COMMIT.lower()
        )
        dist = variants.select(self.settings(), self.pinned, sage=None, frontend_dist=str(self.dist))
        self.assertEqual(variants.resolve(dist, self.resolver_env("fail", git_present=False)).commit, "")

    def test_the_git_environment_is_hardened(self) -> None:
        hardened = variants.git_environment({"PATH": "/bin", "HOME": str(self.scratch.home)})
        self.assertEqual(hardened["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertNotEqual(hardened["HOME"], str(self.scratch.home))

    def source_traces(self, name: str, **overrides: str) -> tuple[list[str], Path, env.Settings]:
        settings = self.settings(CONTAINER_ENGINE=name, **overrides)
        traces: list[str] = []
        original = engine.Engine.command

        def recording(self: engine.Engine, *args: str, **kwargs) -> object:
            result = original(self, *args, capture=True, **kwargs)
            traces.append(result.stdout)
            return result

        with patch.object(engine.Engine, "command", recording):
            dist = variants.build_source(settings, engine.Engine(name, settings), self.pinned, self.source)
        return traces, dist, settings

    def test_build_source_runs_two_phases_and_publishes_by_rename(self) -> None:
        ident = hashlib.sha256(os.fsencode(os.path.realpath(self.source))).hexdigest()[:16]
        cache = Path(os.path.realpath(self.scratch.storage / "cache"))
        writes = str(cache / "frontend-work" / ident / "source" / "dist" / "index.html")
        image = (
            f"docker.io/library/node:{self.pinned['FRONTEND_NODE_TAG']}@sha256:{self.pinned['FRONTEND_NODE_DIGEST']}"
        )
        traces, dist, settings = self.source_traces("docker", FAKE_ENGINE_RUN_WRITES=writes)
        self.assertEqual(dist, cache / "frontend-builds" / ident / "dist")
        self.assertEqual((dist / "index.html").read_text(), "fake engine output\n")
        self.assertFalse((cache / "frontend-work" / ident).exists())
        self.assertFalse((dist.parent / ".dist.staging").exists())
        self.assertEqual(len(traces), 2)
        self.assertIn(f"--user {os.getuid()}:{os.getgid()}", traces[0])
        self.assertNotIn("--network", traces[0])
        self.assertIn("--network none", traces[1])
        for trace in traces:
            self.assertIn(f"--volume {cache}/frontend-work/{ident}:/work", trace)
            self.assertIn(f"--volume {cache}/frontend-pnpm:/pnpm/store", trace)
            self.assertIn(f"--volume {cache}/frontend-tools/{self.pinned['FRONTEND_PNPM_VERSION']}:/tools", trace)
            self.assertIn("--env HOME=/work/home", trace)
            self.assertIn(f"{image} sh -c", trace)
            self.assertNotIn(TOKEN, trace)
        self.assertIn(
            f"sh -c 'export PATH=/tools/bin:$PATH && npm install -g --prefix /tools "
            f"pnpm@{self.pinned['FRONTEND_PNPM_VERSION']} "
            "&& pnpm install --frozen-lockfile --ignore-scripts --store-dir /pnpm/store'",
            traces[0],
        )
        self.assertIn(
            "sh -c 'export PATH=/tools/bin:$PATH && pnpm install --offline --frozen-lockfile --store-dir /pnpm/store "
            "&& pnpm build'",
            traces[1],
        )
        # A second build replaces the published tree; Podman passes no --user.
        (dist / "stale.js").write_text("")
        traces, dist, _ = self.source_traces("podman", FAKE_ENGINE_RUN_WRITES=writes)
        self.assertFalse((dist / "stale.js").exists())
        self.assertNotIn("--user", traces[0] + traces[1])
        # The work copy leaves the history, the modules, and the old output behind.
        for name in (".git", "node_modules", "dist"):
            (self.source / name).mkdir()
            (self.source / name / "file").write_text("")
        (self.source / "nested").mkdir()
        (self.source / "nested" / "dist").mkdir()
        with patch.object(engine.Engine, "command") as command:
            command.side_effect = lambda *args, **kwargs: None
            with self.assertRaisesRegex(
                LatentCrateError, "frontend builder completed without publishing dist/index.html"
            ):
                variants.build_source(settings, engine.Engine("docker", settings), self.pinned, self.source)
        work = cache / "frontend-work" / ident / "source"
        self.assertEqual(sorted(os.listdir(work)), ["build.mjs", "nested", "package.json", "pnpm-lock.yaml"])
        self.assertTrue((work / "nested" / "dist").is_dir())
        self.assertTrue((dist / "index.html").is_file())

    def test_a_dist_reader_never_sees_a_half_tree(self) -> None:
        ident = hashlib.sha256(os.fsencode(os.path.realpath(self.source))).hexdigest()[:16]
        cache = Path(os.path.realpath(self.scratch.storage / "cache"))
        writes = str(cache / "frontend-work" / ident / "source" / "dist" / "index.html")
        _, dist, _ = self.source_traces("podman", FAKE_ENGINE_RUN_WRITES=writes)
        stop = threading.Event()
        listings: list[set[str]] = []

        def read() -> None:
            while not stop.is_set():
                try:
                    listings.append(set(os.listdir(dist)))
                except FileNotFoundError:
                    continue

        reader = threading.Thread(target=read)
        reader.start()
        try:
            for _ in range(20):
                self.source_traces("podman", FAKE_ENGINE_RUN_WRITES=writes)
        finally:
            stop.set()
            reader.join()
        self.assertTrue(listings)
        for listing in listings:
            self.assertIn("index.html", listing)
        self.assertEqual(sorted(path.name for path in dist.parent.iterdir()), ["dist"])

    def test_a_failed_dist_publication_restores_the_previous_tree(self) -> None:
        ident = hashlib.sha256(os.fsencode(os.path.realpath(self.source))).hexdigest()[:16]
        cache = Path(os.path.realpath(self.scratch.storage / "cache"))
        writes = str(cache / "frontend-work" / ident / "source" / "dist" / "index.html")
        _, dist, _ = self.source_traces("podman", FAKE_ENGINE_RUN_WRITES=writes)
        (dist / "index.html").write_text("previous\n", encoding="utf-8")
        renames: list[str] = []
        rename = os.rename

        def failing_second_rename(source, target):
            renames.append(Path(source).name)
            # shutil.move stages the built tree with the first rename.
            if len(renames) == 3:
                raise OSError("simulated rename failure")
            rename(source, target)

        with mock.patch.object(variants.os, "rename", failing_second_rename), self.assertRaises(OSError):
            self.source_traces("podman", FAKE_ENGINE_RUN_WRITES=writes)
        self.assertEqual(renames, ["dist", "dist", ".dist.staging", ".dist.previous"])
        self.assertEqual((dist / "index.html").read_text(encoding="utf-8"), "previous\n")
        self.assertFalse((dist.parent / ".dist.previous").exists())

    def test_every_compose_file_combination_renders_through_the_fake_engine(self) -> None:
        for name in ("docker", "podman"):
            for mode, flag in (("release", {}), ("dist", {"frontend_dist": str(self.dist)})):
                for sage in ("off", "global"):
                    with self.subTest(engine=name, mode=mode, sage=sage):
                        settings = self.settings(CONTAINER_ENGINE=name)
                        variant = variants.select(settings, self.pinned, sage=sage, **flag)
                        trace = (
                            engine.Engine(name, settings)
                            .compose(
                                "config",
                                frontend_dist=variant.dist_overlay,
                                capture=True,
                                **variant.compose_env(self.pinned),
                            )
                            .stdout
                        )
                        files = f"--file compose.yaml --file compose.{name}.yaml --file compose.gpu.yaml"
                        if variant.dist_overlay:
                            files += " --file compose.frontend-dist.yaml"
                        self.assertIn(f"args=compose {files} --env-file pins.env config", trace)
                        self.assertIn(f"image={variant.image(self.pinned)} target={variant.target} env_keys= ", trace)

    def test_compose_yaml_holds_the_comfy_service_only(self) -> None:
        document = yaml.safe_load((SOURCE_ROOT / "compose.yaml").read_text(encoding="utf-8"))
        self.assertEqual(list(document["services"]), ["comfy"])
        self.assertEqual([key for key in document if key.startswith("x-")], [])
        for overlay in ("compose.podman.yaml", "compose.docker.yaml"):
            services = yaml.safe_load((SOURCE_ROOT / overlay).read_text(encoding="utf-8"))["services"]
            self.assertEqual(list(services), ["comfy"], overlay)
