"""`templates list` and `templates create-model-set`: the in-image
`latentcrate-manage-templates.py` run with `engine run` of the built image,
so the template package read is the one installed there. The script reads
nothing but that package, so the container has no network and no mount
except the drafts directory `create-model-set` writes. The image is never
built here: a missing one is refused and `build` named."""

from __future__ import annotations

import dataclasses
import subprocess

from . import LatentCrateError
from .engine import Engine
from .env import Settings
from .names import SET_NAME_RE
from .variants import Variant, host_user, select

SCRIPT = "/usr/local/bin/latentcrate-manage-templates.py"
DRAFTS_DIR = "model-set-drafts"


def release_variant(settings: Settings, pinned: dict[str, str]) -> Variant:
    """The release-frontend variant in the Sage mode of the settings: the
    template package follows the backend image, so a Git, dist, or source
    frontend setting is left aside."""
    release = dataclasses.replace(settings, values=settings.values | {"COMFY_FRONTEND_MODE": "release"})
    return select(release, pinned, sage=None)


def require_image(engine: Engine, image: str) -> None:
    found = engine.command("image", "inspect", "--format", "{{.Id}}", image, capture=True, check=False)
    if found.returncode != 0:
        raise LatentCrateError(f"the image {image} is not built; run: bash bin/latentcrate build")


def run_script(
    engine: Engine, variant: Variant, pinned: dict[str, str], *arguments: str, mounts: tuple[str, ...] = ()
) -> int:
    """The script in a hardened, networkless container of the image (no
    GPU, so no CDI needed) with stdin closed and `mounts` as its only
    volumes; the script's status."""
    image = variant.image(pinned)
    require_image(engine, image)
    result = engine.command(
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "128",
        "--tmpfs",
        "/tmp",
        *host_user(engine),
        *mounts,
        # The image's entrypoint prepares the service's mounts, which this
        # container has not; the venv python on PATH holds the package.
        "--entrypoint",
        "python",
        image,
        SCRIPT,
        *arguments,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode


def list_templates(engine: Engine, variant: Variant, pinned: dict[str, str]) -> int:
    return run_script(engine, variant, pinned, "list")


def create_model_set(
    settings: Settings, engine: Engine, variant: Variant, pinned: dict[str, str], template: str, name: str
) -> int:
    """Write `build/model-set-drafts/<name>.toml` from the template; the
    drafts directory is the only writable mount the run adds."""
    if not SET_NAME_RE.fullmatch(template):
        raise LatentCrateError(f"unsafe template name: {template}")
    if name == "all":
        raise LatentCrateError("all names every model set; choose another --name")
    drafts = settings.build_dir / DRAFTS_DIR
    drafts.mkdir(parents=True, exist_ok=True)
    return run_script(
        engine,
        variant,
        pinned,
        "create-model-set",
        template,
        "--name",
        name,
        mounts=("--volume", f"{drafts}:/output"),
    )
