"""The image variant: the frontend mode (release, git, dist, source) and
the SageAttention mode together name the Dockerfile target, the image tag
`<repository>:<release|git|dist>[-sage]-<COMFYUI_REF>`, and whether the
dist overlay (compose.frontend-dist.yaml) mounts a prebuilt tree. Release
and git are one image target that records `git`; dist and source serve a
host tree, so `release` is a variant name only. A Git reference is
resolved to a commit for `build` and `up` through hardened Git (the
helpers `nodes` shares); source mode builds a checkout into a dist tree
with two `engine run`s of the pinned Node image, the second without a
network, so lifecycle scripts run offline.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import LatentCrateError
from .engine import Engine
from .env import Settings, sage_mode
from .files import publish_tree
from .names import COMMIT_RE

RELEASE_URL = "https://github.com/Comfy-Org/ComfyUI_frontend"
# An HTTPS URL without credentials, query, or fragment.
GIT_URL_RE = re.compile(r"^https://[^/@\s]+/[^?#\s]+$")
NODE_IMAGE = "docker.io/library/node"
# Left out of the work copy: the history, an engine-owned store, and the
# previous output.
SOURCE_EXCLUDED = (".git", "node_modules", ".pnpm-store")

# The Dockerfile's frontend-git-builder stage carries a Bash copy of these
# options; keep the two aligned.
GIT_OPTIONS = (
    "-c",
    "credential.helper=",
    "-c",
    "core.askPass=/bin/false",
    "-c",
    "http.followRedirects=false",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.untrackedCache=false",
)


def git_environment(environ: dict[str, str]) -> dict[str, str]:
    """`environ` with Git kept from prompting, reading the caller's
    configuration, or running hooks."""
    return environ | {
        "GIT_ASKPASS": "/bin/false",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_COUNT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": tempfile.gettempdir(),
    }


def run_git(*arguments: str, env: dict[str, str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run hardened Git with both streams captured; the caller reads the status."""
    return subprocess.run(
        ["git", *GIT_OPTIONS, *arguments],
        env=git_environment(env),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


@dataclass
class Variant:
    frontend: str
    sage: str
    repository: str
    git_url: str = ""
    requested_ref: str = ""
    # Filled by `resolve`; the requested reference is exported until then.
    commit: str = ""
    dist_dir: Path | None = None
    source_dir: Path | None = None

    @property
    def dist_overlay(self) -> bool:
        return self.frontend in ("dist", "source")

    @property
    def name(self) -> str:
        return "dist" if self.dist_overlay else self.frontend

    @property
    def container_mode(self) -> str:
        return "dist" if self.dist_overlay else "git"

    @property
    def target(self) -> str:
        if self.sage != "off":
            return "runtime-sage"
        return "comfy-runtime" if self.dist_overlay else "runtime"

    def image(self, pinned: dict[str, str]) -> str:
        sage = "-sage" if self.sage != "off" else ""
        return f"{self.repository}:{self.name}{sage}-{pinned['COMFYUI_REF']}"

    def compose_env(self, pinned: dict[str, str]) -> dict[str, str]:
        """The values compose.yaml marks as set by the wrapper: the extras
        for `Settings.subprocess_env` and `Engine.compose`. The Sage mode
        in force (the flag over the setting) travels as LATENTCRATE_SAGE,
        which Compose hands to the container as LATENTCRATE_SAGE_MODE."""
        env = {
            "LATENTCRATE_IMAGE": self.image(pinned),
            "COMFY_BUILD_TARGET": self.target,
            "COMFY_FRONTEND_MODE": self.container_mode,
            "FRONTEND_GIT_URL": self.git_url,
            "FRONTEND_GIT_REF": self.commit or self.requested_ref,
            "FRONTEND_GIT_REQUESTED_REF": self.requested_ref,
            "LATENTCRATE_SAGE": self.sage,
        }
        if self.dist_overlay:
            env["COMFY_FRONTEND_DIST_DIR"] = str(self.dist_dir)
        return env


def require_dist_tree(path: str | Path) -> Path:
    """The real path of a frontend dist tree: index.html readable and no
    symbolic link anywhere below, since the tree is bind-mounted."""
    dist = Path(os.path.realpath(path))
    index = dist / "index.html"
    if not index.is_file() or not os.access(index, os.R_OK):
        raise LatentCrateError(f"frontend dist/index.html is missing or unreadable: {dist}")
    for parent, directories, files in os.walk(dist):
        if any(os.path.islink(os.path.join(parent, name)) for name in directories + files):
            raise LatentCrateError(f"frontend dist tree must not contain symbolic links: {dist}")
    return dist


def require_source_tree(path: str) -> Path:
    """The real path of a frontend checkout pnpm can install and build."""
    source = Path(os.path.realpath(path))
    if not source.is_dir():
        raise LatentCrateError(f"frontend source directory does not exist: {source}")
    for name in ("package.json", "pnpm-lock.yaml"):
        if not os.access(source / name, os.R_OK):
            raise LatentCrateError(f"frontend source {name} is missing or unreadable: {source}")
    return source


def source_dist(settings: Settings, source: Path) -> Path:
    """Where a source build publishes its dist tree, keyed by the checkout's real path."""
    ident = hashlib.sha256(os.fsencode(os.path.realpath(source))).hexdigest()[:16]
    return settings.cache_dir / "frontend-builds" / ident / "dist"


def git_reference(url: str, ref: str) -> tuple[str, str]:
    """A public HTTPS repository and a reference Git takes as a plain argument."""
    if not GIT_URL_RE.fullmatch(url):
        raise LatentCrateError("frontend Git URL must use HTTPS; credentials in URLs are not allowed")
    if not ref:
        raise LatentCrateError("frontend Git mode requires a commit, branch, tag, or pull-request reference")
    if ref.startswith("-") or any(char.isspace() for char in ref):
        raise LatentCrateError("frontend Git references must not begin with a dash or contain whitespace")
    return url, ref


def select(
    settings: Settings,
    pinned: dict[str, str],
    *,
    sage: str | None,
    frontend_git: tuple[str, str] | None = None,
    frontend_dist: str | None = None,
    frontend_source: str | None = None,
) -> Variant:
    """The variant a command runs: a frontend flag wins over
    COMFY_FRONTEND_MODE and its companion settings, `sage` over LATENTCRATE_SAGE."""
    flags = [flag for flag in (frontend_git, frontend_dist, frontend_source) if flag is not None]
    if len(flags) > 1:
        raise LatentCrateError("choose one of --frontend-git, --frontend-dist, and --frontend-source")
    variant = Variant(
        frontend=settings.get("COMFY_FRONTEND_MODE", "release"),
        sage=sage_mode(sage) if sage is not None else settings.sage,
        repository=settings.get("LATENTCRATE_IMAGE") or "latentcrate/comfy",
    )
    if frontend_git is not None:
        variant.frontend = "git"
    elif frontend_dist is not None:
        variant.frontend = "dist"
    elif frontend_source is not None:
        variant.frontend = "source"
    if variant.frontend == "release":
        variant.git_url, variant.requested_ref = git_reference(RELEASE_URL, pinned["COMFYUI_FRONTEND_REF"])
    elif variant.frontend == "git":
        url, ref = frontend_git or (settings.get("FRONTEND_GIT_URL"), settings.get("FRONTEND_GIT_REF"))
        variant.git_url, variant.requested_ref = git_reference(url, ref)
    elif variant.frontend == "dist":
        dist = frontend_dist or settings.get("COMFY_FRONTEND_DIST_DIR")
        if not dist:
            raise LatentCrateError("dist frontend mode requires COMFY_FRONTEND_DIST_DIR or --frontend-dist")
        variant.dist_dir = require_dist_tree(dist)
    elif variant.frontend == "source":
        source = frontend_source or settings.get("COMFY_FRONTEND_SOURCE_DIR")
        if not source:
            raise LatentCrateError("frontend source mode requires COMFY_FRONTEND_SOURCE_DIR or --frontend-source")
        variant.source_dir = require_source_tree(source)
        variant.dist_dir = source_dist(settings, variant.source_dir)
    else:
        raise LatentCrateError(f"unsupported COMFY_FRONTEND_MODE={variant.frontend}")
    return variant


def resolve(variant: Variant, env: dict[str, str]) -> Variant:
    """The variant with its Git reference resolved to a commit (`build` and
    `up`; every other rendering command exports the requested reference).
    A full commit is taken as written; a branch or tag is listed with
    `git ls-remote` under `env`, the peeled line of an annotated tag wins,
    and a name both a branch and a tag carry is refused."""
    if not variant.git_url:
        return variant
    ref = variant.requested_ref
    if COMMIT_RE.fullmatch(ref.lower()):
        variant.commit = ref.lower()
        return variant
    if shutil.which("git", path=env.get("PATH")) is None:
        raise LatentCrateError("git is required to resolve a frontend branch, tag, or pull-request reference")
    # An empty working directory keeps a repository's local configuration
    # out of the listing.
    with tempfile.TemporaryDirectory() as boundary:
        listed = run_git(
            "ls-remote",
            variant.git_url,
            ref,
            f"refs/heads/{ref}",
            f"refs/tags/{ref}",
            f"refs/tags/{ref}^{{}}",
            env=env,
            cwd=Path(boundary),
        )
    if listed.returncode != 0:
        raise LatentCrateError(f"could not query frontend reference {ref} from {variant.git_url}")
    lines = [line.split("\t", 1) for line in listed.stdout.splitlines() if "\t" in line]
    if not lines:
        raise LatentCrateError(f"frontend reference was not found: {ref}")
    if len({commit for commit, name in lines if not name.endswith("^{}")}) > 1:
        raise LatentCrateError(f"frontend reference {ref} is ambiguous; write refs/heads/{ref} or refs/tags/{ref}")
    resolved = next((commit for commit, name in lines if name.endswith("^{}")), lines[0][0]).lower()
    if not COMMIT_RE.fullmatch(resolved):
        raise LatentCrateError(f"frontend reference did not resolve to a commit: {ref}")
    variant.commit = resolved
    return variant


def copy_source(source: Path, work: Path) -> None:
    """The checkout's files into the work copy, symbolic links kept as
    links, the excluded names and the top-level dist left out."""

    def excluded(directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in SOURCE_EXCLUDED or (name == "dist" and Path(directory) == source)}

    shutil.copytree(source, work, symlinks=True, ignore=excluded)


def build_source(settings: Settings, engine: Engine, pinned: dict[str, str], source_dir: Path) -> Path:
    """Build a frontend checkout into its dist tree under the cache
    directory and return that tree. Phase 1 (network) installs pnpm into a
    per-version tools prefix and the lockfile's packages into the store
    without running scripts; phase 2 (`--network none`) installs offline
    and builds. The checkout is copied on the host, so no container sees
    it. The dist is published by two renames (the previous tree aside,
    the staged one in), so a reader sees the previous tree or the
    complete new one."""
    dist = source_dist(settings, source_dir)
    ident = dist.parent.name
    cache = settings.cache_dir
    store = cache / "frontend-pnpm"
    tools = cache / "frontend-tools" / pinned["FRONTEND_PNPM_VERSION"]
    work = cache / "frontend-work" / ident
    if work.exists():
        shutil.rmtree(work)
    for directory in (store, tools, work, dist.parent):
        directory.mkdir(parents=True, exist_ok=True)
    copy_source(source_dir, work / "source")
    image = f"{NODE_IMAGE}:{pinned['FRONTEND_NODE_TAG']}@sha256:{pinned['FRONTEND_NODE_DIGEST']}"
    # Docker runs the container as the calling user so the mounts stay the
    # user's; rootless Podman maps container root to the host user already.
    user = ["--user", f"{os.getuid()}:{os.getgid()}"] if engine.name == "docker" else []
    options = [
        "run",
        "--rm",
        *user,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp",
        "--env",
        "HOME=/work/home",
        "--env",
        "CI=true",
        # Nested pnpm calls inherit it: keep the pinned pnpm when package.json
        # names another, since the offline phase cannot download one.
        "--env",
        "PNPM_CONFIG_PM_ON_FAIL=ignore",
        "--volume",
        f"{work}:/work",
        "--volume",
        f"{store}:/pnpm/store",
        "--volume",
        f"{tools}:/tools",
        "--workdir",
        "/work/source",
    ]
    # The checkout's scripts call `pnpm` by name, so the tools prefix leads PATH.
    path = "export PATH=/tools/bin:$PATH"
    engine.command(
        *options,
        image,
        "sh",
        "-c",
        f"{path} && npm install -g --prefix /tools pnpm@{pinned['FRONTEND_PNPM_VERSION']} "
        "&& pnpm install --frozen-lockfile --ignore-scripts --store-dir /pnpm/store",
    )
    engine.command(
        *options,
        "--network",
        "none",
        image,
        "sh",
        "-c",
        f"{path} && pnpm install --offline --frozen-lockfile --store-dir /pnpm/store && pnpm build",
    )
    built = work / "source" / "dist"
    if not (built / "index.html").is_file():
        raise LatentCrateError("frontend builder completed without publishing dist/index.html")
    staging = dist.parent / ".dist.staging"
    previous = dist.parent / ".dist.previous"
    for leftover in (staging, previous):
        if leftover.exists():
            shutil.rmtree(leftover)
    shutil.move(built, staging)
    require_dist_tree(staging)
    publish_tree(staging, dist, previous)
    shutil.rmtree(work)
    return dist
