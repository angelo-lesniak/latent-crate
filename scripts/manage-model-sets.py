#!/usr/bin/env python3
"""Fetch and verify pinned Hugging Face files declared by model-set manifests."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import re
import shutil
import stat
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

try:
    import fcntl
except ModuleNotFoundError:  # Windows hosts only import this module for static tests.
    fcntl = None


SET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
WORKFLOW_URL_RE = re.compile(
    r"^https://github\.com/Comfy-Org/workflow_templates/blob/"
    r"[0-9a-f]{40}/templates/[A-Za-z0-9_.-]+\.json$"
)
LICENSE_URL_RE = re.compile(
    r"^(?:"
    r"https://huggingface\.co/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/blob/[0-9a-f]{40}/[^\s]+"
    r"|https://cdn\.jsdelivr\.net/gh/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}/[^\s]+"
    r")$"
)


class ModelSetError(RuntimeError):
    """A safe, user-facing model-set error."""


@dataclass(frozen=True)
class License:
    name: str
    url: str


@dataclass(frozen=True)
class ModelFile:
    repository: str
    revision: str
    source: str
    destination: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ModelSet:
    name: str
    description: str
    workflow_urls: tuple[str, ...]
    licenses: tuple[License, ...]
    files: tuple[ModelFile, ...]


def safe_relative_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ModelSetError(f"{field} must be a non-empty path")
    if "\\" in value or any(ord(character) < 32 for character in value):
        raise ModelSetError(f"{field} contains unsupported characters: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ModelSetError(f"{field} must be a safe relative path: {value!r}")
    return path.as_posix()


def require_keys(table: dict, allowed: set[str], context: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ModelSetError(f"unknown field(s) in {context}: {', '.join(sorted(unknown))}")


def read_manifest(path: Path) -> ModelSet:
    name = path.stem
    if not SET_NAME_RE.fullmatch(name):
        raise ModelSetError(f"unsafe model-set filename: {path.name}")
    if name == "all":
        raise ModelSetError("all is reserved and cannot be a model-set filename")
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ModelSetError(f"could not read {path}: {error}") from error
    if not isinstance(document, dict):
        raise ModelSetError(f"{path} must contain a TOML table")
    require_keys(document, {"schema", "description", "workflow_urls", "license", "file"}, name)
    if document.get("schema") != 1:
        raise ModelSetError(f"{name}: schema must be 1")
    description = document.get("description")
    workflow_urls = document.get("workflow_urls")
    if not isinstance(description, str) or not description.strip():
        raise ModelSetError(f"{name}: description must be a non-empty string")
    if (
        not isinstance(workflow_urls, list)
        or not workflow_urls
        or not all(isinstance(url, str) and WORKFLOW_URL_RE.fullmatch(url) for url in workflow_urls)
        or len(set(workflow_urls)) != len(workflow_urls)
    ):
        raise ModelSetError(f"{name}: workflow_urls must contain unique, commit-pinned official ComfyUI templates")

    raw_licenses = document.get("license")
    if not isinstance(raw_licenses, list) or not raw_licenses:
        raise ModelSetError(f"{name}: at least one [[license]] entry is required")
    licenses: list[License] = []
    for index, raw in enumerate(raw_licenses, start=1):
        if not isinstance(raw, dict):
            raise ModelSetError(f"{name}: license {index} must be a table")
        require_keys(raw, {"name", "url"}, f"{name} license {index}")
        license_name = raw.get("name")
        url = raw.get("url")
        if not isinstance(license_name, str) or not license_name.strip():
            raise ModelSetError(f"{name}: license {index} needs a name")
        if not isinstance(url, str) or LICENSE_URL_RE.fullmatch(url) is None:
            raise ModelSetError(f"{name}: license {index} needs an immutable approved HTTPS URL")
        licenses.append(License(license_name.strip(), url))

    raw_files = document.get("file")
    if not isinstance(raw_files, list) or not raw_files:
        raise ModelSetError(f"{name}: at least one [[file]] entry is required")
    files: list[ModelFile] = []
    destinations: set[str] = set()
    for index, raw in enumerate(raw_files, start=1):
        if not isinstance(raw, dict):
            raise ModelSetError(f"{name}: file {index} must be a table")
        require_keys(
            raw,
            {"repository", "revision", "source", "destination", "size", "sha256"},
            f"{name} file {index}",
        )
        repository = raw.get("repository")
        revision = raw.get("revision")
        source = safe_relative_path(raw.get("source"), f"{name} file {index} source")
        destination = safe_relative_path(raw.get("destination"), f"{name} file {index} destination")
        size = raw.get("size")
        digest = raw.get("sha256")
        if not isinstance(repository, str) or not REPOSITORY_RE.fullmatch(repository):
            raise ModelSetError(f"{name}: file {index} has an invalid repository")
        if not isinstance(revision, str) or not REVISION_RE.fullmatch(revision):
            raise ModelSetError(f"{name}: file {index} revision must be a full lowercase commit")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ModelSetError(f"{name}: file {index} size must be a positive integer")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ModelSetError(f"{name}: file {index} sha256 must be lowercase hexadecimal")
        if destination in destinations:
            raise ModelSetError(f"{name}: duplicate destination: {destination}")
        destinations.add(destination)
        files.append(ModelFile(repository, revision, source, destination, size, digest))
    return ModelSet(name, description.strip(), tuple(workflow_urls), tuple(licenses), tuple(files))


def available_sets(manifest_dir: Path) -> dict[str, ModelSet]:
    sets: dict[str, ModelSet] = {}
    for path in sorted(manifest_dir.glob("*.toml")):
        model_set = read_manifest(path)
        sets[model_set.name] = model_set
    if not sets:
        raise ModelSetError(f"no model sets found in {manifest_dir}")
    return sets


def select_sets(catalog: dict[str, ModelSet], names: list[str]) -> list[ModelSet]:
    if not names:
        raise ModelSetError("provide one or more model-set names, or all")
    if "all" in names:
        if len(names) != 1:
            raise ModelSetError("all cannot be combined with named model sets")
        return list(catalog.values())
    selected: list[ModelSet] = []
    seen: set[str] = set()
    for name in names:
        if not SET_NAME_RE.fullmatch(name):
            raise ModelSetError(f"unsafe model-set name: {name}")
        if name not in catalog:
            raise ModelSetError(f"unknown model set: {name} (available: {', '.join(catalog)})")
        if name not in seen:
            selected.append(catalog[name])
            seen.add(name)
    return selected


def merged_files(selected: list[ModelSet]) -> list[ModelFile]:
    by_destination: dict[str, ModelFile] = {}
    for model_set in selected:
        for model_file in model_set.files:
            previous = by_destination.get(model_file.destination)
            if previous is not None and previous != model_file:
                raise ModelSetError(
                    f"selected sets disagree about {model_file.destination}; fetch them separately or fix the manifests"
                )
            by_destination[model_file.destination] = model_file
    return list(by_destination.values())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def allowed_roots(models_root: Path) -> tuple[Path, ...]:
    roots = [models_root.resolve()]
    for raw in os.environ.get("MODEL_SET_EXTRA_WRITE_ROOTS", "").split(":"):
        if not raw:
            continue
        candidate = Path(raw)
        if not candidate.is_absolute() or not candidate.is_dir():
            raise ModelSetError(f"MODEL_SET_EXTRA_WRITE_ROOTS entry is not an absolute directory: {raw}")
        roots.append(candidate.resolve())
    return tuple(roots)


def is_within(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def destination_path(
    models_root: Path,
    destination: str,
    roots: tuple[Path, ...],
    *,
    create_parent: bool,
) -> Path:
    path = models_root.joinpath(*PurePosixPath(destination).parts)
    ancestor = path.parent
    while not os.path.lexists(ancestor):
        if ancestor.parent == ancestor:
            raise ModelSetError(f"destination has no existing parent: {destination}")
        ancestor = ancestor.parent
    if ancestor.is_symlink() and not ancestor.exists():
        raise ModelSetError(f"destination uses a dangling directory symlink: {destination}")
    resolved_ancestor = ancestor.resolve(strict=True)
    if not is_within(resolved_ancestor, roots):
        raise ModelSetError(
            f"destination escapes the allowed model storage: {destination}; mount and allow its symlink target explicitly"
        )
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.exists():
        parent = path.parent.resolve(strict=True)
        if not is_within(parent, roots):
            raise ModelSetError(
                f"destination escapes the allowed model storage: {destination}; "
                "mount and allow its symlink target explicitly"
            )
    if path.is_symlink():
        resolved = path.resolve(strict=False)
        if not is_within(resolved, roots):
            raise ModelSetError(f"destination symlink escapes the allowed model storage: {destination}")
    return path


@contextlib.contextmanager
def model_download_lock(models_root: Path):
    if fcntl is None:
        raise ModelSetError("model downloads require a Linux container")
    lock_path = models_root / ".latentcrate-model-set.lock"
    if lock_path.is_symlink():
        raise ModelSetError(f"refusing unsafe lock symlink: {lock_path}")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise ModelSetError(f"could not open the model-set lock: {error}") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ModelSetError(f"model-set lock is not a regular file: {lock_path}")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ModelSetError("another model-set download is using this models directory") from error
        yield
    finally:
        os.close(descriptor)


def file_state(path: Path, expected: ModelFile) -> str:
    if path.is_symlink() and not path.exists():
        return "dangling symlink"
    if not path.exists():
        return "missing"
    if not path.is_file():
        return "not a regular file"
    if path.stat().st_size != expected.size:
        return "wrong size"
    if sha256_file(path) != expected.sha256:
        return "wrong checksum"
    return "ready"


def print_notices(selected: list[ModelSet]) -> None:
    print("Review and accept the model licenses before downloading:")
    seen: set[tuple[str, str]] = set()
    for model_set in selected:
        for license_entry in model_set.licenses:
            key = (license_entry.name, license_entry.url)
            if key not in seen:
                print(f"  {license_entry.name}: {license_entry.url}")
                seen.add(key)
    print("Pinned official workflows:")
    seen_workflows: set[str] = set()
    for model_set in selected:
        for workflow_url in model_set.workflow_urls:
            if workflow_url not in seen_workflows:
                print(f"  {model_set.name}: {workflow_url}")
                seen_workflows.add(workflow_url)


def prepare_staging_directory(path: Path) -> None:
    current_uid = os.getuid() if hasattr(os, "getuid") else None
    if os.path.lexists(path):
        path_stat = path.lstat()
        if not stat.S_ISDIR(path_stat.st_mode) or path.is_symlink():
            raise ModelSetError(f"refusing unsafe staging path: {path}")
        if current_uid is not None and path_stat.st_uid != current_uid:
            raise ModelSetError(f"staging path is owned by another user: {path}")
    else:
        path.mkdir(mode=0o700)
    path.chmod(0o700)

    for directory, directory_names, file_names in os.walk(path, followlinks=False):
        for name in directory_names + file_names:
            entry = Path(directory) / name
            entry_stat = entry.lstat()
            if stat.S_ISLNK(entry_stat.st_mode):
                raise ModelSetError(f"refusing symlink inside resumable staging: {entry}")
            if current_uid is not None and entry_stat.st_uid != current_uid:
                raise ModelSetError(f"staging entry is owned by another user: {entry}")
            if stat.S_ISDIR(entry_stat.st_mode):
                entry.chmod(0o700)
            elif not stat.S_ISREG(entry_stat.st_mode):
                raise ModelSetError(f"refusing unsafe staging entry: {entry}")


def remove_staging_directory(staging: Path, staging_root: Path) -> None:
    shutil.rmtree(staging)
    try:
        staging_root.rmdir()
    except OSError:
        pass


def require_atomic_publication_support(pending: list[tuple[ModelFile, Path]]) -> None:
    checked_parents: set[Path] = set()
    for _, destination in pending:
        parent = destination.parent.resolve(strict=True)
        if parent in checked_parents:
            continue
        checked_parents.add(parent)
        descriptor, raw_source = tempfile.mkstemp(prefix=".latentcrate-link-test-", dir=parent)
        os.close(descriptor)
        source = Path(raw_source)
        target = source.with_name(f"{source.name}.link")
        try:
            os.link(source, target, follow_symlinks=False)
        except OSError as error:
            raise ModelSetError(
                f"model storage at {parent} must allow temporary files and hard links "
                f"for atomic model publication ({error})"
            ) from error
        finally:
            target.unlink(missing_ok=True)
            source.unlink(missing_ok=True)


def read_token_from_stdin(enabled: bool) -> str | None:
    if not enabled:
        return None
    token = sys.stdin.read().strip()
    return token or None


def hf_download(model_file: ModelFile, staging: Path | None, token: str | None, dry_run: bool = False):
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as error:
        raise ModelSetError("the model-set tool image is missing huggingface_hub") from error
    arguments = dict(
        repo_id=model_file.repository,
        filename=model_file.source,
        revision=model_file.revision,
        token=token,
        dry_run=dry_run,
    )
    if staging is not None:
        arguments["local_dir"] = staging
    return hf_hub_download(**arguments)


def fetch(selected: list[ModelSet], models_root: Path, token: str | None) -> None:
    roots = allowed_roots(models_root)
    files = merged_files(selected)
    print_notices(selected)

    pending: list[tuple[ModelFile, Path]] = []
    for model_file in files:
        destination = destination_path(models_root, model_file.destination, roots, create_parent=True)
        state = file_state(destination, model_file)
        if state == "ready":
            print(f"ready: {model_file.destination}")
        else:
            if state != "missing":
                raise ModelSetError(
                    f"{model_file.destination} has the {state}; move it aside before downloading the pinned file"
                )
            pending.append((model_file, destination))
    if not pending:
        print("All selected model files are ready.")
        return

    require_atomic_publication_support(pending)
    print("Checking access to every selected Hugging Face file before downloading ...")
    for model_file, _ in pending:
        try:
            hf_download(model_file, None, token, dry_run=True)
        except Exception as error:  # huggingface_hub has several network/auth exception types
            raise ModelSetError(
                f"cannot access {model_file.repository}/{model_file.source} at {model_file.revision}. "
                f"For restricted repositories, accept the license and set HF_TOKEN in .env. ({error})"
            ) from error

    for model_file, destination in pending:
        key = hashlib.sha256(
            f"{model_file.repository}\0{model_file.revision}\0{model_file.source}".encode()
        ).hexdigest()[:24]
        # Stage beside the final destination so verified publication can use
        # one atomic, no-clobber hard link even through a category symlink.
        staging_root = destination.parent / ".latentcrate-downloads"
        prepare_staging_directory(staging_root)
        staging = staging_root / key
        prepare_staging_directory(staging)
        resolved_staging = staging.resolve(strict=True)
        if not is_within(resolved_staging, roots):
            raise ModelSetError(f"staging directory escapes the allowed model storage: {model_file.destination}")
        print(f"downloading: {model_file.destination} ({model_file.size / 1_000_000_000:.1f} GB)")
        try:
            downloaded = Path(hf_download(model_file, staging, token))
        except Exception as error:
            raise ModelSetError(f"download failed for {model_file.destination}: {error}") from error
        resolved_download = downloaded.resolve(strict=True)
        downloaded_stat = downloaded.lstat()
        if not stat.S_ISREG(downloaded_stat.st_mode) or not is_within(
            resolved_download, (resolved_staging,)
        ):
            raise ModelSetError(f"download helper returned an unsafe file path: {model_file.destination}")
        if downloaded.stat().st_size != model_file.size:
            remove_staging_directory(staging, staging_root)
            raise ModelSetError(f"downloaded size does not match the manifest: {model_file.destination}")
        if sha256_file(downloaded) != model_file.sha256:
            remove_staging_directory(staging, staging_root)
            raise ModelSetError(f"downloaded checksum does not match the manifest: {model_file.destination}")
        try:
            os.link(downloaded, destination, follow_symlinks=False)
        except FileExistsError as error:
            if file_state(destination, model_file) != "ready":
                raise ModelSetError(
                    f"{model_file.destination} appeared while downloading and was not overwritten; review it and retry"
                ) from error
        downloaded.unlink()
        remove_staging_directory(staging, staging_root)
        print(f"installed: {model_file.destination}")
    print("Selected model sets are ready. Refresh the ComfyUI browser tab if it is already open.")


def status(selected: list[ModelSet], models_root: Path) -> int:
    roots = allowed_roots(models_root)
    result = 0
    for model_file in merged_files(selected):
        path = destination_path(models_root, model_file.destination, roots, create_parent=False)
        state = file_state(path, model_file)
        print(f"{model_file.destination}: {state}")
        if state != "ready":
            result = 1
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fetch", "status"))
    parser.add_argument("sets", nargs="+")
    parser.add_argument("--manifest-dir", type=Path, default=Path("/config/model-sets"))
    parser.add_argument("--models-root", type=Path, default=Path("/models"))
    parser.add_argument("--token-stdin", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        catalog = available_sets(args.manifest_dir)
        selected = select_sets(catalog, args.sets)
        if not args.models_root.is_dir():
            raise ModelSetError(f"models directory does not exist: {args.models_root}")
        if args.action == "fetch":
            with model_download_lock(args.models_root):
                fetch(selected, args.models_root, read_token_from_stdin(args.token_stdin))
            return 0
        return status(selected, args.models_root)
    except ModelSetError as error:
        print(f"LatentCrate: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"LatentCrate: filesystem operation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
