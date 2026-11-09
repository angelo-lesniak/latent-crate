"""Keep the on-disk model library: destinations, allowed roots, the download lock, verification stamps, staging."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .. import LatentCrateError
from ..files import write_atomic
from .manifest import ModelFile

# Written beside a file once its checksum matched; see file_state.
STAMP_SUFFIX = ".latentcrate-verified"
LOCK_NAME = ".latentcrate-model-set.lock"
STAGING_NAME = ".latentcrate-downloads"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def allowed_roots(models_root: Path, extra: str) -> tuple[Path, ...]:
    """The models directory plus MODEL_SET_EXTRA_WRITE_ROOTS, the colon-separated
    absolute directories a destination may reach through a symlink."""
    roots = [models_root.resolve()]
    for raw in extra.split(":"):
        if not raw:
            continue
        candidate = Path(raw)
        if not candidate.is_absolute() or not candidate.is_dir():
            raise LatentCrateError(f"MODEL_SET_EXTRA_WRITE_ROOTS entry is not an absolute directory: {raw}")
        roots.append(candidate.resolve())
    return tuple(roots)


def is_within(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def destination_path(models_root: Path, destination: str, roots: tuple[Path, ...]) -> Path:
    """The library path of a destination, checked but not created; fetch creates the parent when it stages."""
    path = models_root.joinpath(*PurePosixPath(destination).parts)
    ancestor = path.parent
    while not os.path.lexists(ancestor):
        if ancestor.parent == ancestor:
            raise LatentCrateError(f"destination has no existing parent: {destination}")
        ancestor = ancestor.parent
    if ancestor.is_symlink() and not ancestor.exists():
        raise LatentCrateError(f"destination uses a dangling directory symlink: {destination}")
    remedy = "list its symlink target in MODEL_SET_EXTRA_WRITE_ROOTS"
    if not is_within(ancestor.resolve(strict=True), roots):
        raise LatentCrateError(f"destination escapes the allowed model storage: {destination}; {remedy}")
    if path.is_symlink() and not is_within(path.resolve(strict=False), roots):
        raise LatentCrateError(f"destination symlink escapes the allowed model storage: {destination}; {remedy}")
    return path


@contextlib.contextmanager
def model_download_lock(models_root: Path):
    lock_path = models_root / LOCK_NAME
    if lock_path.is_symlink():
        raise LatentCrateError(f"refusing unsafe lock symlink: {lock_path}")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise LatentCrateError(f"model-set lock is not a regular file: {lock_path}")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LatentCrateError("another model-set download is using this models directory") from error
        yield
    finally:
        os.close(descriptor)


def stamp_path(path: Path) -> Path:
    return path.with_name(path.name + STAMP_SUFFIX)


def stamp_of(path_stat: os.stat_result, sha256: str) -> dict:
    """What a stamp records: the checksum, and the size and mtime of the file it was computed from."""
    return {"sha256": sha256, "size": path_stat.st_size, "mtime_ns": path_stat.st_mtime_ns}


def read_stamp(path: Path) -> dict | None:
    """The stamp beside a file, or None when it is missing or unreadable."""
    try:
        return json.loads(stamp_path(path).read_bytes())
    except (OSError, ValueError):
        return None


def write_stamp(path: Path, sha256: str) -> None:
    """Record the verified file beside it; a library that cannot be written is hashed again next time."""
    with contextlib.suppress(OSError):
        write_atomic(stamp_path(path), json.dumps(stamp_of(path.stat(), sha256)), mode=0o600)


def is_stamped(path: Path, path_stat: os.stat_result, expected: ModelFile) -> bool:
    return read_stamp(path) == stamp_of(path_stat, expected.sha256)


def file_state(path: Path, expected: ModelFile, *, verify: bool = False) -> str:
    """Classify the file at a destination.

    Hashing a model library takes minutes per 10 GB, so a file is hashed
    once: the match is recorded in a stamp beside it, and later calls trust
    the stamp while the file's size, mtime, and the manifest's checksum are
    the recorded ones. The container mounts the library writable, so a
    same-size swap that preserves the timestamp passes the stamp; ``verify``
    hashes regardless and is the remedy."""
    if path.is_symlink() and not path.exists():
        return "dangling symlink"
    if not path.exists():
        return "missing"
    if not path.is_file():
        return "not a regular file"
    path_stat = path.stat()
    if path_stat.st_size != expected.size:
        return "wrong size"
    if verify or not is_stamped(path, path_stat, expected):
        if sha256_file(path) != expected.sha256:
            return "wrong checksum"
        write_stamp(path, expected.sha256)
    return "ready"


def announce_hashing(located: list[tuple[Path, ModelFile]], verify: bool = False) -> None:
    """Say up front how many present files file_state is about to hash."""
    to_hash = [
        model_file
        for path, model_file in located
        if path.is_file() and (verify or not is_stamped(path, path.stat(), model_file))
    ]
    if to_hash:
        total = sum(model_file.size for model_file in to_hash) / 1_000_000_000
        print(
            f"verifying: {len(to_hash)} file(s) ({total:.1f} GB) by checksum; "
            "later runs trust the record written beside each verified file"
        )


def prepare_staging_directory(path: Path) -> None:
    current_uid = os.getuid()
    if os.path.lexists(path):
        path_stat = path.lstat()
        if not stat.S_ISDIR(path_stat.st_mode) or path.is_symlink():
            raise LatentCrateError(f"refusing unsafe staging path: {path}")
        if path_stat.st_uid != current_uid:
            raise LatentCrateError(f"staging path is owned by another user: {path}")
    else:
        path.mkdir(mode=0o700)
    path.chmod(0o700)

    for directory, directory_names, file_names in os.walk(path, followlinks=False):
        for name in directory_names + file_names:
            entry = Path(directory) / name
            entry_stat = entry.lstat()
            if stat.S_ISLNK(entry_stat.st_mode):
                raise LatentCrateError(f"refusing symlink inside resumable staging: {entry}")
            if entry_stat.st_uid != current_uid:
                raise LatentCrateError(f"staging entry is owned by another user: {entry}")
            if stat.S_ISDIR(entry_stat.st_mode):
                entry.chmod(0o700)
            elif not stat.S_ISREG(entry_stat.st_mode):
                raise LatentCrateError(f"refusing unsafe staging entry: {entry}")


def remove_staging_directory(staging: Path, staging_root: Path) -> None:
    """Remove one file's staging directory, and the root beside the
    destination unless other downloads still stage there."""
    shutil.rmtree(staging)
    try:
        staging_root.rmdir()
    except OSError as error:
        if error.errno != errno.ENOTEMPTY:
            raise


def staging_directory(model_file: ModelFile, destination: Path) -> Path:
    """The resumable staging directory of one pinned file, beside its destination.

    Staging next to the final path lets verified publication be one atomic
    rename; the key names the pinned file, so a manifest change never resumes
    a stale partial download."""
    pinned = f"{model_file.repository}\0{model_file.revision}\0{model_file.source}"
    return destination.parent / STAGING_NAME / hashlib.sha256(pinned.encode()).hexdigest()[:24]


def staged_paths(model_file: ModelFile, staging: Path) -> tuple[Path, Path]:
    """The complete-download path and the in-progress .part path inside staging."""
    staged = staging / PurePosixPath(model_file.destination).name
    return staged, staged.with_name(staged.name + ".part")


def staged_size(path: Path) -> int:
    """Bytes in a staging file; 0 when it is absent or not a regular file."""
    try:
        path_stat = path.lstat()
    except OSError:
        return 0
    return path_stat.st_size if stat.S_ISREG(path_stat.st_mode) else 0


@dataclass(frozen=True)
class PendingFile:
    """A selected file that is not at its destination, classified by what its staging holds.

    The state is "complete" when the whole download awaits verification,
    "partial" when a .part file can be resumed, and "absent" otherwise;
    ``received`` counts the pinned file's bytes already in staging. Only the
    bytes beyond them need free space, and only an incomplete file needs the
    Hub."""

    model_file: ModelFile
    destination: Path
    staging: Path
    state: str
    received: int

    @property
    def remaining(self) -> int:
        return max(self.model_file.size - self.received, 0)


def pending_file(model_file: ModelFile, destination: Path) -> PendingFile:
    staging = staging_directory(model_file, destination)
    staged, part = staged_paths(model_file, staging)
    if staged_size(staged) == model_file.size:
        return PendingFile(model_file, destination, staging, "complete", model_file.size)
    received = staged_size(part)
    return PendingFile(model_file, destination, staging, "partial" if received else "absent", received)


def require_free_space(models_root: Path, pending: list[PendingFile]) -> None:
    """Refuse to start when the bytes still to download exceed the free space."""
    needed = sum(item.remaining for item in pending)
    free = shutil.disk_usage(models_root).free
    if needed > free:
        raise LatentCrateError(
            f"the selected files need {needed / 1_000_000_000:.1f} GB but only "
            f"{free / 1_000_000_000:.1f} GB are free below {models_root}; make room or fetch fewer sets"
        )
