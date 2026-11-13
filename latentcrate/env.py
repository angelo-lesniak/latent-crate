"""Local settings: `.env`, the shell environment, the storage paths, and the
pins in `pins.env`.

Precedence is the same for the wrapper and for Compose: a shell environment
variable overrides `.env` for one command, and `.env` overrides the defaults
in `.env.example`, which is the defaults layer itself (a deleted line
changes nothing). Every key the example file sets is exported to Compose
with its effective value, so Compose never applies a fallback of its own; a
`.env` key the example does not list is refused. The
pins in `pins.env` cannot be overridden: every pin key is removed from the
environment handed to a subprocess, because the shell wins over
`--env-file` and Compose must read the file's value. The Hugging Face and
Civitai tokens are moved out of the settings, this process's environment
and the environment of every subprocess into `Settings.hf_token` and
`Settings.civitai_token`, which the model downloader reads. The container identity (`HOST_UID`, `HOST_GID`) is the
calling user, never a setting. `.env` is the one file that may hold a
credential; `require_private` is the check that it is the calling user's
and readable by that user alone, and `Settings.storage` the one list of
the host paths the wrapper owns or mounts with the rules that apply to
each. The bind address and the port are validated by their properties, so
`load` never refuses a setting `doctor` only reports.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from . import LatentCrateError
from .files import read_utf8
from .names import COMMIT_RE, SHA256_RE

# The model downloader's tokens: read into `Settings.hf_token` and
# `Settings.civitai_token`, never a setting, never exported.
TOKEN_KEYS = ("HF_TOKEN", "CIVITAI_TOKEN")
# Host paths, resolved from the repository root when relative and to their
# real path always, so the mounts and the overlap rule see one spelling of
# a directory.
PATH_KEYS = ("COMFY_DATA_DIR", "COMFY_MODELS_DIR", "COMFY_CACHE_DIR", "COMFY_LOCAL_NODES_DIR")
# The switch the wrapper itself reads (Compose reads the rest as text):
# a misspelling must not silently mean false.
BOOLEAN_KEYS = ("COMFY_ALLOW_REMOTE",)
BOOLEAN_WORDS = {"1": True, "true": True, "yes": True, "on": True} | dict.fromkeys(("0", "false", "no", "off"), False)
# What podman-compose keeps of a project name (Docker Compose's rule is the
# same); anything else would silently name another project's containers.
PROJECT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
ENV_LINE_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
# A pin line as written: no prefix, an upper-case key, nothing trimmed.
PIN_LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
# A setting `.env.example` documents, commented out or not.
DOCUMENTED_KEY_RE = re.compile(r"^#?\s*([A-Za-z_][A-Za-z0-9_]*)=")
# A port as Compose publishes it: no leading zero, so the value is used as
# written and never normalised.
PORT_RE = re.compile(r"^[1-9][0-9]{0,4}$")
# CUDA architectures as the SageAttention and node builds take them and as
# the Dockerfile's pip cache-mount id embeds them: `;`-separated, no
# whitespace or comma.
CUDA_ARCH_LIST_RE = re.compile(r"^\d+\.\d+(\+PTX)?(;\d+\.\d+(\+PTX)?)*$")
# The SageAttention modes the entrypoint reads, and how a refusal lists them.
SAGE_MODES = ("off", "available", "global")
SAGE_WORDS = f"{', '.join(SAGE_MODES[:-1])}, or {SAGE_MODES[-1]}"
# The bind addresses that need no acknowledgement; Compose publishes an
# IPv6 address in its bracket form only.
LOOPBACK = ("127.0.0.1", "[::1]")
BIND_NOT_ACCEPTED = "COMFY_BIND_ADDRESS=localhost is not a binding Compose accepts; use 127.0.0.1 or [::1]"

# pins.env is self-describing by its key names: `<NAME>_TAG` is an image tag
# and pairs with `<NAME>_DIGEST`, the bare sha256 hex of the manifest the tag
# points at (the Dockerfile adds the `@sha256:` prefix), because a tag can be
# moved and the digest is what the build pulls; `<NAME>_REF` is a Git tag of
# the repository the Dockerfile names and pairs with `<NAME>_COMMIT`, the
# full lowercase commit the tag names, because a tag can be moved too and
# the build refuses a checkout at another commit; `<NAME>_VERSION` is a
# package version; CUDA_MIN_DRIVER_MAJOR is the driver major `doctor`
# checks. Every key exactly once, a plain value without quotes or
# whitespace, and no other key shape. Compose reads the file itself through
# --env-file and passes every pin as a build argument; the wrapper reads it
# for the keys below (the family rule, the image tag, the frontend commit,
# `doctor`) and for `pins check`.
PIN_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*_(TAG|DIGEST|VERSION|REF|COMMIT)$")
WRAPPER_PIN_KEYS = (
    "PYTORCH_DEVEL_TAG",
    "PYTORCH_RUNTIME_TAG",
    "TORCHCODEC_VERSION",
    "CUDA_NPP_VERSION",
    "COMFYUI_REF",
    "COMFYUI_FRONTEND_COMMIT",
    "CUDA_MIN_DRIVER_MAJOR",
)
# Image tags, package versions, Git references, digests, and integers: no
# quotes, no whitespace.
PIN_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
# The CUDA family inside a PyTorch image tag (`<torch>-cuda13.0-cudnn9-runtime`).
CUDA_FAMILY_RE = re.compile(r"-cuda(\d+)\.(\d+)-")
FAMILY_RULE = (
    "PYTORCH_DEVEL_TAG, PYTORCH_RUNTIME_TAG, TORCHCODEC_VERSION, and CUDA_NPP_VERSION in pins.env "
    "must name one PyTorch release and CUDA family; change the four pins together"
)


def is_true(value: str, key: str) -> bool:
    """A switch the wrapper reads: one of the eight words, or a refusal."""
    try:
        return BOOLEAN_WORDS[value.strip().lower()]
    except KeyError:
        raise LatentCrateError(f"{key} must be one of 1/true/yes/on or 0/false/no/off, not {value!r}") from None


def sage_mode(value: str) -> str:
    """The SageAttention mode the setting names, lower-cased."""
    mode = value.strip().lower()
    if mode not in SAGE_MODES:
        raise LatentCrateError(f"LATENTCRATE_SAGE must be {SAGE_WORDS}")
    return mode


def not_loopback(address: str) -> str:
    """The refusal for a bind address beyond loopback without the acknowledgement."""
    return (
        f"COMFY_BIND_ADDRESS={address} is not loopback; "
        "set COMFY_ALLOW_REMOTE=true only after adding authentication and TLS"
    )


def require_private(path: Path, info: os.stat_result) -> None:
    """A file that may hold a credential (`.env`) is the calling user's and
    readable by that user alone (SECURITY.md); `info` is its `os.stat`."""
    if info.st_uid != os.getuid():
        raise LatentCrateError(f"{path} must be owned by your user (uid {os.getuid()}), since it may hold credentials")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise LatentCrateError(f"{path} must be readable by your user only; run: chmod 600 {path}")


def parse_env_file(path: Path, *, plain: bool = False) -> dict[str, str]:
    """Read `KEY=VALUE` lines, each key once. Comment lines start with `#`;
    a value may be wrapped in matching single or double quotes; nothing is
    expanded or executed; a byte-order mark is skipped. With `plain` (the
    pins file), keys are upper case and a value is a bare token: no quotes,
    no whitespace."""
    values: dict[str, str] = {}
    for number, raw in enumerate(read_utf8(path).splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # A pin is matched as written: a stray space is refused, not trimmed.
        match = PIN_LINE_RE.match(raw) if plain else ENV_LINE_RE.match(line)
        if match is None:
            raise LatentCrateError(f"{path}:{number}: expected KEY=VALUE")
        key, value = match.group(1), match.group(2) if plain else match.group(2).strip()
        if key in values:
            raise LatentCrateError(f"{path}:{number}: {key} is set twice; keep one line")
        if plain:
            if not PIN_VALUE_RE.fullmatch(value):
                raise LatentCrateError(
                    f"pins file has an unsafe value for {key} (quotes and whitespace are not allowed): {path}"
                )
            values[key] = value
            continue
        quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"
        if quoted:
            value = value[1:-1]
        elif "$" in value or value.startswith("~") or " #" in value:
            raise LatentCrateError(
                f"{path}:{number}: {key}: values are used literally; write the full value "
                "and keep comments on their own line"
            )
        values[key] = value
    return values


def documented_keys(example: Path) -> set[str]:
    """Every key `.env.example` lists, commented out or not, so a key that
    neither the wrapper nor the Compose files read is refused instead of
    silently doing nothing."""
    lines = example.read_text(encoding="utf-8").splitlines()
    return {match.group(1) for line in lines if (match := DOCUMENTED_KEY_RE.match(line.strip()))}


def load_pins(path: Path) -> dict[str, str]:
    """Read pins.env: every key a pin by the naming convention above, in file order."""
    if not path.is_file():
        raise LatentCrateError(f"pins file is missing: {path}")
    values = parse_env_file(path, plain=True)
    missing = [key for key in WRAPPER_PIN_KEYS if key not in values]
    if missing:
        raise LatentCrateError(f"pins file must define {', '.join(missing)}: {path}")
    unknown = sorted(key for key in values if key != "CUDA_MIN_DRIVER_MAJOR" and not PIN_KEY_RE.fullmatch(key))
    if unknown:
        raise LatentCrateError(
            f"pins file defines unknown key(s) {', '.join(unknown)} "
            "(a pin is <NAME>_TAG with <NAME>_DIGEST, <NAME>_REF with <NAME>_COMMIT, <NAME>_VERSION, "
            f"or CUDA_MIN_DRIVER_MAJOR): {path}"
        )
    if not values["CUDA_MIN_DRIVER_MAJOR"].isdigit():
        raise LatentCrateError(f"CUDA_MIN_DRIVER_MAJOR must be a driver major version: {path}")
    for key, value in values.items():
        if key.endswith("_DIGEST"):
            if not SHA256_RE.fullmatch(value):
                raise LatentCrateError(f"{key} must be the 64 hex characters of a sha256 digest: {path}")
            if key.removesuffix("_DIGEST") + "_TAG" not in values:
                raise LatentCrateError(f"{key} has no {key.removesuffix('_DIGEST')}_TAG beside it: {path}")
        elif key.endswith("_TAG") and key.removesuffix("_TAG") + "_DIGEST" not in values:
            raise LatentCrateError(
                f"{key} has no {key.removesuffix('_TAG')}_DIGEST beside it (the build pulls the digest): {path}"
            )
        elif key.endswith("_COMMIT"):
            if not COMMIT_RE.fullmatch(value):
                raise LatentCrateError(f"{key} must be the 40 lowercase hex characters of a commit: {path}")
            if key.removesuffix("_COMMIT") + "_REF" not in values:
                raise LatentCrateError(f"{key} has no {key.removesuffix('_COMMIT')}_REF beside it: {path}")
        elif key.endswith("_REF") and key.removesuffix("_REF") + "_COMMIT" not in values:
            raise LatentCrateError(
                f"{key} has no {key.removesuffix('_REF')}_COMMIT beside it (the build checks out the commit): {path}"
            )
    cuda_family(values)
    return values


def pin_keys(path: Path) -> tuple[str, ...]:
    """The keys of the `KEY=VALUE` lines of pins.env, as a scan that refuses
    nothing and is empty for a missing file: `subprocess_env` removes them
    whatever else the file holds, since a recovery command (`down`,
    `status`, `logs`) runs without validating the pins."""
    try:
        lines = read_utf8(path).splitlines()
    except FileNotFoundError:
        return ()
    return tuple(match.group(1) for line in lines if (match := PIN_LINE_RE.match(line)))


def cuda_family(pinned: dict[str, str]) -> str:
    """The CUDA family (`cu130`) the four PyTorch pins name together: the
    runtime tag carries it, the devel tag is the same release, TorchCodec's
    `+cuNNN` suffix and the NPP major follow it."""
    runtime = pinned.get("PYTORCH_RUNTIME_TAG", "")
    match = CUDA_FAMILY_RE.search(runtime)
    if match is None:
        raise LatentCrateError(FAMILY_RULE)
    major, minor = match.groups()
    family = f"cu{major}{minor}"
    if (
        pinned.get("PYTORCH_DEVEL_TAG", "").removesuffix("-devel") != runtime.removesuffix("-runtime")
        or pinned.get("TORCHCODEC_VERSION", "").rpartition("+")[2] != family
        or pinned.get("CUDA_NPP_VERSION", "").split(".", 1)[0] != major
    ):
        raise LatentCrateError(FAMILY_RULE)
    return family


@dataclass(frozen=True)
class HostPath:
    """One host path the wrapper owns or mounts, and the rules that apply
    to it: `mounted` into the ComfyUI container (it must not contain the
    checkout, whose `.env` would otherwise reach the container, and no two
    mounted paths overlap), `writable` by it (what `doctor` checks, read
    access otherwise), and `created_by_init`. `key` is the setting that
    names it, if one does."""

    label: str
    path: Path
    key: str = ""
    mounted: bool = False
    writable: bool = False
    created_by_init: bool = False


@dataclass
class Settings:
    root: Path
    values: dict[str, str]
    # The keys `.env.example` sets: what the wrapper defaults and exports.
    defaults: dict[str, str]
    # The `.env` keys the example file lists; an unlisted key is refused.
    file_values: dict[str, str]
    environ: dict[str, str]
    # The tokens found in the shell environment or `.env`, if any.
    hf_token: str | None = field(repr=False)
    civitai_token: str | None = field(repr=False)

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    @property
    def data_dir(self) -> Path:
        return Path(self.values["COMFY_DATA_DIR"])

    @property
    def models_dir(self) -> Path:
        return Path(self.values["COMFY_MODELS_DIR"])

    @property
    def cache_dir(self) -> Path:
        return Path(self.values["COMFY_CACHE_DIR"])

    @property
    def local_nodes_dir(self) -> Path:
        return Path(self.values["COMFY_LOCAL_NODES_DIR"])

    @property
    def managed_nodes_dir(self) -> Path:
        return self.data_dir / "custom_nodes"

    @property
    def project_name(self) -> str:
        return self.values["COMPOSE_PROJECT_NAME"]

    @property
    def allow_remote(self) -> bool:
        return is_true(self.values["COMFY_ALLOW_REMOTE"], "COMFY_ALLOW_REMOTE")

    @property
    def gpu_device(self) -> str:
        return self.values["GPU_DEVICE"]

    @property
    def cuda_arch_list(self) -> str:
        value = self.values["COMFY_CUDA_ARCH_LIST"]
        if not CUDA_ARCH_LIST_RE.fullmatch(value):
            raise LatentCrateError(
                f"COMFY_CUDA_ARCH_LIST={value!r} is not a ;-separated list of CUDA architectures "
                "such as 12.0 or 8.6+PTX"
            )
        return value

    @property
    def sage(self) -> str:
        return sage_mode(self.values["LATENTCRATE_SAGE"])

    @property
    def host_model_gid(self) -> str:
        value = self.values.get("HOST_MODEL_GID")
        if value is None:
            return str(os.getgid())
        if not value.isdigit():
            raise LatentCrateError("HOST_MODEL_GID must be a numeric group id")
        return value

    @property
    def bind_address(self) -> str:
        address = self.values["COMFY_BIND_ADDRESS"]
        if address in LOOPBACK:
            return address
        if address == "localhost":
            raise LatentCrateError(BIND_NOT_ACCEPTED)
        if not self.allow_remote:
            raise LatentCrateError(not_loopback(address))
        return address

    @property
    def port(self) -> str:
        return validate_port(self.values["COMFY_PORT"], "COMFY_PORT")

    @property
    def service_url(self) -> str:
        return f"http://{self.bind_address}:{self.port}"

    @property
    def build_dir(self) -> Path:
        return self.root / "build"

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def local_model_sets_dir(self) -> Path:
        return self.root / "local" / "model-sets"

    @property
    def storage(self) -> tuple[HostPath, ...]:
        """Every host path the wrapper owns or mounts, with its rules: the
        one list that `load`, `init`, and `doctor` iterate. The storage
        directories are real paths already; the checkout is resolved here
        so the rules see one spelling."""
        root = Path(os.path.realpath(self.root))
        mounted = {"mounted": True, "created_by_init": True}
        return (
            HostPath("the LatentCrate checkout", root),
            HostPath("the data directory", self.data_dir, "COMFY_DATA_DIR", writable=True, **mounted),
            HostPath("the managed custom-node directory", self.managed_nodes_dir, created_by_init=True),
            HostPath("the models directory", self.models_dir, "COMFY_MODELS_DIR", writable=True, **mounted),
            HostPath("the cache directory", self.cache_dir, "COMFY_CACHE_DIR", writable=True, **mounted),
            HostPath("the local custom-node directory", self.local_nodes_dir, "COMFY_LOCAL_NODES_DIR", **mounted),
            HostPath("the private model-set directory", self.local_model_sets_dir, created_by_init=True),
        )

    def subprocess_env(self, **extra: str) -> dict[str, str]:
        """The environment for every subprocess: the documented `.env`
        values, the shell environment on top, the pins removed (Compose
        reads pins.env through --env-file, and the shell would win over
        it), every key the wrapper defaults with its effective value, and
        the calling user as the container identity (and, unset, as
        HOST_MODEL_GID). The tokens are in none
        of those (`load` moved them into `hf_token` and `civitai_token`). Exporting the effective
        values keeps Compose from applying a default of its own."""
        env = dict(self.file_values)
        env.update(self.environ)
        for key in pin_keys(self.root / "pins.env"):
            env.pop(key, None)
        for key in self.defaults:
            env[key] = self.values[key]
        env["LATENTCRATE_ROOT"] = str(self.root)
        env["HOST_UID"] = str(os.getuid())
        env["HOST_GID"] = str(os.getgid())
        # Docker maps no host group into the container, so the caller's
        # group is the default model-storage group.
        env.setdefault("HOST_MODEL_GID", env["HOST_GID"])
        env.update(extra)
        return env


def load(root: Path, environ: dict[str, str] | None = None) -> Settings:
    if environ is None:
        environ = dict(os.environ)
        # Every subprocess inherits this process's environment, so the
        # tokens leave it here; the downloader gets them as an argument.
        for key in TOKEN_KEYS:
            os.environ.pop(key, None)
    else:
        environ = dict(environ)
    example = root / ".env.example"
    if not example.is_file():
        raise LatentCrateError(f"the checkout has no settings reference: {example}")
    defaults = parse_env_file(example)
    env_file = root / ".env"
    if env_file.is_symlink():
        raise LatentCrateError(f"refusing symbolic link for local settings: {env_file}")
    file_values = {}
    if env_file.exists():
        if not env_file.is_file():
            raise LatentCrateError(f"{env_file} is not a file")
        # It may hold the tokens; `init` creates it with mode 0600.
        require_private(env_file, os.stat(env_file))
        file_values = parse_env_file(env_file)
    documented = documented_keys(example)
    unknown = sorted(key for key in file_values if key not in documented)
    # An unlisted key is a typo or an engine variable that belongs in the shell.
    if unknown:
        raise LatentCrateError(
            f".env sets {', '.join(unknown)}, which .env.example does not list; "
            "remove the line or set the variable in the shell instead"
        )
    # The shell wins over the file, as for every setting; an empty shell
    # value means "no token" and suppresses the file's.
    tokens = {}
    for key in TOKEN_KEYS:
        defaults.pop(key, None)
        tokens[key] = environ.pop(key, file_values.pop(key, None)) or None

    values = dict(defaults)
    values.update(file_values)
    values.update(environ)
    project_name = values.get("COMPOSE_PROJECT_NAME") or defaults["COMPOSE_PROJECT_NAME"]
    if not PROJECT_NAME_RE.fullmatch(project_name):
        raise LatentCrateError(
            f"COMPOSE_PROJECT_NAME must be lowercase letters, digits, - and _, starting with a letter or digit: "
            f"{project_name!r}"
        )
    values["COMPOSE_PROJECT_NAME"] = project_name
    if values.get("COMFY_BIND_ADDRESS") == "::1":
        values["COMFY_BIND_ADDRESS"] = "[::1]"
    # Relative paths are relative to the repository root, whichever
    # directory the wrapper runs from.
    for key in PATH_KEYS:
        value = values[key]
        if not value:
            raise LatentCrateError(f"{key} must not be empty; omit the line to take the default ({defaults[key]})")
        if value.startswith("~"):
            raise LatentCrateError(f"{key} must not start with ~; write the full path")
        values[key] = os.path.realpath(value if value.startswith("/") else root / value.removeprefix("./"))
    for key in BOOLEAN_KEYS:
        is_true(values[key], key)
    settings = Settings(root, values, defaults, file_values, environ, tokens["HF_TOKEN"], tokens["CIVITAI_TOKEN"])
    # The checkout (with .env) is what the next start trusts; a storage
    # directory that is or contains it would be mounted into the container.
    checkout = Path(os.path.realpath(root))
    mounted = [place for place in settings.storage if place.mounted]
    for place in mounted:
        if checkout.is_relative_to(place.path):
            raise LatentCrateError(f"{place.key} must not be or contain the LatentCrate checkout: {place.path}")
    # A directory mounted twice is written through two mounts, and the
    # managed custom nodes must not be the local ones.
    for outer in mounted:
        for inner in mounted:
            if inner is not outer and inner.path.is_relative_to(outer.path):
                raise LatentCrateError(
                    f"{outer.key} and {inner.key} must be separate directories, neither inside the other: "
                    f"{outer.path}, {inner.path}"
                )
    return settings


def validate_port(value: str, label: str) -> str:
    if not PORT_RE.fullmatch(value) or int(value) > 65535:
        raise LatentCrateError(f"{label} requires a port from 1 to 65535")
    return value
