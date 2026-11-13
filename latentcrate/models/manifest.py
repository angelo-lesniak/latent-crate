"""Read and validate model-set manifests into :class:`ModelSet` records."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .. import LatentCrateError
from ..names import COMMIT_RE, SET_NAME_RE, SHA256_RE, load_catalog, read_toml, relative_path
from ..names import description as read_description

REPOSITORY_RE = re.compile(r"^(?!\.{1,2}/)[A-Za-z0-9_.-]+/(?!\.{1,2}$)[A-Za-z0-9_.-]+$")
WORKFLOW_URL_RE = re.compile(
    r"^https://github\.com/Comfy-Org/workflow_templates/blob/[0-9a-f]{40}/templates/[A-Za-z0-9_.-]+\.json$"
)
LICENSE_URL_RE = re.compile(
    r"^(?:"
    r"https://huggingface\.co/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/blob/[0-9a-f]{40}/[^\s]+"
    r"|https://cdn\.jsdelivr\.net/gh/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}/[^\s]+"
    r"|https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/blob/[0-9a-f]{40}/[^\s]+"
    r")$"
)
# A model page is edited in place by its uploader and has no pinned form,
# so only a private set may name one as its license. civitai.red is left
# out: Cloudflare challenges its pages over HTTP/1.1, so check --remote
# could never reach them.
CIVITAI_MODEL_URL_RE = re.compile(r"^https://civitai\.com/models/[1-9][0-9]*$")


@dataclass(frozen=True)
class License:
    name: str
    url: str


@dataclass(frozen=True)
class ModelFile:
    # A Hugging Face file names repository, revision, and source; a Civitai
    # file names civitai_version, and civitai_file for a non-primary file,
    # and leaves the three None.
    repository: str | None
    revision: str | None
    source: str | None
    destination: str
    # None when a private set leaves the pin out; see read_file.
    size: int | None
    sha256: str | None
    civitai_version: int | None = None
    civitai_file: int | None = None

    @property
    def origin(self) -> str:
        if self.civitai_version is not None:
            return f"Civitai model version {self.civitai_version}"
        return f"{self.repository}/{self.source} at {self.revision}"


@dataclass(frozen=True)
class ModelSet:
    name: str
    description: str
    workflow_urls: tuple[str, ...]
    licenses: tuple[License, ...]
    files: tuple[ModelFile, ...]
    private: bool = False


def require_keys(table: dict, allowed: set[str], context: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise LatentCrateError(f"unknown field(s) in {context}: {', '.join(sorted(unknown))}")


def read_manifest(path: Path, *, private: bool = False) -> ModelSet:
    """The set a manifest declares; ``private`` marks a set under local/model-sets, whose files may lack pins."""
    name = path.stem
    if not SET_NAME_RE.fullmatch(name):
        raise LatentCrateError(f"unsafe model-set filename: {path.name}")
    if name == "all":
        raise LatentCrateError("all is reserved and cannot be a model-set filename")
    document = read_toml(path, {"schema", "description", "workflow_urls", "license", "file"}, name)
    description = read_description(document, name)
    workflow_urls = document.get("workflow_urls")
    if (
        not isinstance(workflow_urls, list)
        or not workflow_urls
        or not all(isinstance(url, str) and WORKFLOW_URL_RE.fullmatch(url) for url in workflow_urls)
        or len(set(workflow_urls)) != len(workflow_urls)
    ):
        raise LatentCrateError(f"{name}: workflow_urls must contain unique, commit-pinned official ComfyUI templates")

    raw_licenses = document.get("license")
    if not isinstance(raw_licenses, list) or not raw_licenses:
        raise LatentCrateError(f"{name}: at least one [[license]] entry is required")
    licenses = [read_license(raw, index, name, private) for index, raw in enumerate(raw_licenses, start=1)]

    raw_files = document.get("file")
    if not isinstance(raw_files, list) or not raw_files:
        raise LatentCrateError(f"{name}: at least one [[file]] entry is required")
    files: list[ModelFile] = []
    destinations: set[str] = set()
    for index, raw in enumerate(raw_files, start=1):
        model_file = read_file(raw, index, name, private)
        if model_file.destination in destinations:
            raise LatentCrateError(f"{name}: duplicate destination: {model_file.destination}")
        destinations.add(model_file.destination)
        files.append(model_file)
    return ModelSet(name, description, tuple(workflow_urls), tuple(licenses), tuple(files), private)


def read_license(raw: object, index: int, name: str, private: bool) -> License:
    """One [[license]] entry: a name and an immutable approved HTTPS URL, or
    for a private set also a Civitai model page."""
    if not isinstance(raw, dict):
        raise LatentCrateError(f"{name}: license {index} must be a table")
    require_keys(raw, {"name", "url"}, f"{name} license {index}")
    license_name = raw.get("name")
    url = raw.get("url")
    if not isinstance(license_name, str) or not license_name.strip():
        raise LatentCrateError(f"{name}: license {index} needs a name")
    if not isinstance(url, str) or not (
        LICENSE_URL_RE.fullmatch(url) or (private and CIVITAI_MODEL_URL_RE.fullmatch(url))
    ):
        raise LatentCrateError(f"{name}: license {index} needs an immutable approved HTTPS URL")
    return License(license_name.strip(), url)


def read_file(raw: object, index: int, name: str, private: bool) -> ModelFile:
    """One [[file]] entry: a Hugging Face file pinned to a commit, or a
    Civitai model version, and its path below the models directory.

    A private set may leave ``size`` and ``sha256`` out: its author is the
    only user and would compute both from the same download, so the first
    download is trusted and recorded instead (library.file_state)."""
    if not isinstance(raw, dict):
        raise LatentCrateError(f"{name}: file {index} must be a table")
    context = f"{name} file {index}"
    hub_keys = {"repository", "revision", "source"}
    require_keys(raw, {*hub_keys, "civitai_version", "civitai_file", "destination", "size", "sha256"}, context)
    destination = relative_path(raw.get("destination"), "destination", context)
    size = raw.get("size")
    digest = raw.get("sha256")
    version = raw.get("civitai_version")
    file_id = raw.get("civitai_file")
    repository = revision = source = None
    if "civitai_version" in raw:
        if hub_keys & set(raw):
            raise LatentCrateError(f"{name}: file {index} names civitai_version beside repository, revision, or source")
        if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
            raise LatentCrateError(f"{name}: file {index} civitai_version must be a positive integer")
        if "civitai_file" in raw and (not isinstance(file_id, int) or isinstance(file_id, bool) or file_id <= 0):
            raise LatentCrateError(f"{name}: file {index} civitai_file must be a positive integer")
    elif "civitai_file" in raw:
        raise LatentCrateError(f"{name}: file {index} names civitai_file without civitai_version")
    else:
        repository = raw.get("repository")
        revision = raw.get("revision")
        source = relative_path(raw.get("source"), "source", context)
        if not isinstance(repository, str) or not REPOSITORY_RE.fullmatch(repository):
            raise LatentCrateError(f"{name}: file {index} has an invalid repository")
        if not isinstance(revision, str) or not COMMIT_RE.fullmatch(revision):
            raise LatentCrateError(f"{name}: file {index} revision must be a full lowercase commit")
    if not (private and size is None) and (not isinstance(size, int) or isinstance(size, bool) or size <= 0):
        raise LatentCrateError(f"{name}: file {index} size must be a positive integer")
    if not (private and digest is None) and (not isinstance(digest, str) or not SHA256_RE.fullmatch(digest)):
        raise LatentCrateError(f"{name}: file {index} sha256 must be lowercase hexadecimal")
    return ModelFile(repository, revision, source, destination, size, digest, version, file_id)


def catalog(config_dir: Path, local_dir: Path | None = None) -> dict[str, ModelSet]:
    """Every shipped model set under config_dir/model-sets, then the private
    sets under local_dir (absent or empty is fine), by name; a name in both
    is refused."""
    sets = load_catalog(config_dir / "model-sets", read_manifest, "model set")
    private = sorted(local_dir.glob("*.toml"), key=lambda item: item.stem) if local_dir is not None else []
    for path in private:
        model_set = read_manifest(path, private=True)
        if model_set.name in sets:
            shipped = config_dir / "model-sets" / path.name
            raise LatentCrateError(f"model set {model_set.name} is declared twice: {shipped} and {path}")
        sets[model_set.name] = model_set
    return sets


def merged_files(selected: list[ModelSet]) -> list[ModelFile]:
    by_destination: dict[str, ModelFile] = {}
    for model_set in selected:
        for model_file in model_set.files:
            previous = by_destination.get(model_file.destination)
            if previous is not None and previous != model_file:
                raise LatentCrateError(
                    f"selected sets disagree about {model_file.destination}; fetch them separately or fix the manifests"
                )
            by_destination[model_file.destination] = model_file
    return list(by_destination.values())
