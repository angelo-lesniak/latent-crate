"""`templates list` and `templates create-model-set`: the in-image
`latentcrate-manage-templates.py` run as a one-off `compose run` of the
ComfyUI service, so the template package read is the one installed in the
built image and the run inherits the service's hardening, mounts, and
network. The image is never built here: a missing one is refused and
`build` named."""

from __future__ import annotations

import dataclasses
import subprocess

from . import LatentCrateError
from .engine import SERVICE, Engine
from .env import Settings
from .names import SET_NAME_RE
from .variants import Variant, select

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


def run_script(engine: Engine, variant: Variant, pinned: dict[str, str], *arguments: str) -> int:
    """The script in a one-off container of the service, without the GPU
    overlay (no CDI needed) and with stdin closed; the script's status."""
    require_image(engine, variant.image(pinned))
    result = engine.compose(
        "run",
        "--rm",
        "--no-deps",
        "-T",
        *arguments,
        gpu=False,
        stdin=subprocess.DEVNULL,
        check=False,
        **variant.compose_env(pinned),
    )
    return result.returncode


def list_templates(engine: Engine, variant: Variant, pinned: dict[str, str]) -> int:
    return run_script(engine, variant, pinned, "--entrypoint", "python", SERVICE, SCRIPT, "list")


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
        "--volume",
        f"{drafts}:/output",
        "--entrypoint",
        "python",
        SERVICE,
        SCRIPT,
        "create-model-set",
        template,
        "--name",
        name,
    )
