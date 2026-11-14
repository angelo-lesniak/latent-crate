"""Serve manifest authors: draft a [[file]] entry from a host's metadata and check manifests against the hosts."""

from __future__ import annotations

import http.client
import re
import urllib.error
from pathlib import PurePosixPath

from .. import LatentCrateError
from ..names import COMMIT_RE, RELATIVE_PATH_RE, relative_path
from . import hub
from .hub import Tokens, civitai_files, civitai_record, hub_json, open_url, revision_url, version_file, version_url
from .manifest import REPOSITORY_RE, ModelFile, ModelSet

GITHUB_BLOB_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/blob/([0-9a-f]{40})/(.+)$")


def toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def siblings_of(payload: dict) -> dict[str, dict]:
    return {
        item["rfilename"]: item
        for item in payload.get("siblings", [])
        if isinstance(item, dict) and isinstance(item.get("rfilename"), str)
    }


def draft_civitai(version: int, tokens: Tokens) -> str:
    payload = hub_json(version_url(version), tokens)
    item = version_file(version, payload)
    name = item.get("name")
    size, digest = civitai_record(item)
    model = payload.get("modelId")
    safe_name = isinstance(name, str) and RELATIVE_PATH_RE.fullmatch(name) and "/" not in name
    lines = [
        "[[file]]",
        f"civitai_version = {version}",
        *([f"# Model page and license: {hub.CIVITAI_URL}/models/{model}"] if isinstance(model, int) else []),
        "# TODO: prefix the ComfyUI model folder (diffusion_models/, text_encoders/, vae/, loras/)",
        f"destination = {toml_string(name)}" if safe_name else 'destination = "TODO"',
        f"size = {size}" if size is not None else 'size = "TODO"',
        f"sha256 = {toml_string(digest)}" if digest is not None else 'sha256 = "TODO"',
    ]
    others = [other for other in civitai_files(payload) if other is not item]
    if others:
        lines.append("# Other files of this version; to take one, set civitai_file and its destination, size, sha256:")
    for other in others:
        other_size, other_digest = civitai_record(other)
        lines.append(
            f"#   civitai_file = {other.get('id')!r}: {other.get('name')!r}, "
            f"size {other_size or 'unknown'}, sha256 {other_digest or 'unknown'}"
        )
    return "\n".join(lines) + "\n"


def draft(spec: str, tokens: Tokens) -> str:
    civitai = re.fullmatch(r"civitai:(?P<version>[1-9][0-9]{0,17})", spec)
    if civitai:
        return draft_civitai(int(civitai.group("version")), tokens)
    match = re.fullmatch(r"hf:(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+):(?P<file>\S+)", spec)
    if match is None or not REPOSITORY_RE.fullmatch(match.group("repository")):
        raise LatentCrateError("draft needs hf:<owner>/<repository>:<file> or civitai:<model version id>")
    repository = match.group("repository")
    wanted = relative_path(match.group("file"), "file", "draft")
    payload = hub_json(revision_url(repository, "main"), tokens)
    revision = payload.get("sha")
    if not isinstance(revision, str) or not COMMIT_RE.fullmatch(revision):
        raise LatentCrateError("Hugging Face did not report a commit for the main branch")
    siblings = siblings_of(payload)
    if wanted not in siblings:
        raise LatentCrateError(f"{wanted} is not in {repository} at {revision}")
    item = siblings[wanted]
    lfs = item.get("lfs") or {}
    size = item.get("size") or lfs.get("size")
    digest = lfs.get("sha256")
    lines = [
        "[[file]]",
        f"repository = {toml_string(repository)}",
        f"revision = {toml_string(revision)}",
        f"source = {toml_string(wanted)}",
        "# TODO: prefix the ComfyUI model folder (diffusion_models/, text_encoders/, vae/, loras/)",
        f"destination = {toml_string(PurePosixPath(wanted).name)}",
        # Quoted so the draft parses and fails per field, not per file.
        f"size = {size}" if isinstance(size, int) else 'size = "TODO"',
        f"sha256 = {toml_string(digest)}" if isinstance(digest, str) else 'sha256 = "TODO"',
    ]
    return "\n".join(lines) + "\n"


def remote_mismatch(model_file: ModelFile, siblings: dict[str, dict]) -> str | None:
    """How the Hub's record of a pinned file disagrees with the manifest, or None."""
    remote = siblings.get(model_file.source)
    if remote is None:
        return "missing from the repository at the pinned revision"
    size = remote.get("size")
    if model_file.size is not None and size != model_file.size:
        return f"remote size {size}, manifest {model_file.size}"
    # The Hub records a SHA-256 for LFS files only; a plain Git blob is
    # judged by its size here and by its checksum at fetch.
    digest = (remote.get("lfs") or {}).get("sha256")
    if None not in (digest, model_file.sha256) and digest != model_file.sha256:
        return f"remote sha256 {digest}, manifest {model_file.sha256}"
    return None


def civitai_mismatch(model_file: ModelFile, payload: dict) -> str | None:
    """How Civitai's record of a pinned version's chosen file disagrees with the manifest, or None."""
    size, digest = civitai_record(version_file(model_file.civitai_version, payload, model_file.civitai_file))
    if model_file.size is not None and size != model_file.size:
        return f"remote size {size}, manifest {model_file.size}"
    if None not in (digest, model_file.sha256) and digest != model_file.sha256:
        return f"remote sha256 {digest}, manifest {model_file.sha256}"
    return None


def link_failure(url: str, tokens: Tokens) -> str | None:
    """Why a HEAD of a pinned link did not answer 200, or None. A GitHub blob
    page is fetched as its raw file, which answers HEAD without rendering."""
    blob = GITHUB_BLOB_RE.fullmatch(url)
    if blob:
        url = f"https://raw.githubusercontent.com/{blob.group(1)}/{blob.group(2)}/{blob.group(3)}"
    try:
        with open_url(url, {}, tokens, method="HEAD") as response:
            return None if response.status == 200 else f"HTTP {response.status}"
    except urllib.error.HTTPError as error:
        return f"HTTP {error.code}"
    except (OSError, http.client.HTTPException) as error:
        raise LatentCrateError(f"could not reach {url}: {error}") from error


def check(selected: list[ModelSet], tokens: Tokens, *, remote: bool) -> int:
    """Report the loaded manifests; with ``remote``, compare every pinned file
    with its host's size and checksum and HEAD every workflow and license link.
    Returns 1 when anything disagrees or a link fails."""
    for model_set in selected:
        print(f"{model_set.name}: {len(model_set.files)} file(s), {len(model_set.licenses)} license(s)")
    if not remote:
        return 0
    result = 0
    pinned: dict[tuple[str, str], list[ModelFile]] = {}
    versions: dict[int, list[ModelFile]] = {}
    links: list[str] = []
    for model_set in selected:
        for model_file in model_set.files:
            if model_file.civitai_version is not None:
                versions.setdefault(model_file.civitai_version, []).append(model_file)
            else:
                pinned.setdefault((model_file.repository, model_file.revision), []).append(model_file)
        links.extend((*model_set.workflow_urls, *(entry.url for entry in model_set.licenses)))
    for (repository, revision), model_files in pinned.items():
        siblings = siblings_of(hub_json(revision_url(repository, revision), tokens))
        for model_file in model_files:
            mismatch = remote_mismatch(model_file, siblings)
            print(f"{repository}/{model_file.source}: {mismatch or 'ok'}")
            result |= mismatch is not None
    for version, model_files in versions.items():
        payload = hub_json(version_url(version), tokens)
        for model_file in model_files:
            mismatch = civitai_mismatch(model_file, payload)
            print(f"civitai:{version} {model_file.destination}: {mismatch or 'ok'}")
            result |= mismatch is not None
    for url in dict.fromkeys(links):
        failure = link_failure(url, tokens)
        print(f"{url}: {failure or 'ok'}")
        result |= failure is not None
    return int(result)
