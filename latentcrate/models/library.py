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
from ..names import SHA256_RE
from .manifest import LOCK_NAME, STAGING_NAME, STAMP_SUFFIX, ModelFile


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
    with contextlib.suppress(LatentCrateError):
        write_atomic(stamp_path(path), json.dumps(stamp_of(path.stat(), sha256)), mode=0o600)


def reference_sha256(path: Path, expected: ModelFile) -> str | None:
    """The checksum a file is judged against: the manifest's pin, or for a
    file without one the checksum its stamp recorded; None when neither exists."""
    if expected.sha256 is not None:
        return expected.sha256
    stamp = read_stamp(path)
    digest = stamp.get("sha256") if isinstance(stamp, dict) else None
    return digest if isinstance(digest, str) and SHA256_RE.fullmatch(digest) else None


def is_stamped(path: Path, path_stat: os.stat_result, reference: str) -> bool:
    return read_stamp(path) == stamp_of(path_stat, reference)


def file_state(path: Path, expected: ModelFile, *, verify: bool = False) -> str:
    """Classify the file at a destination.

    Hashing a model library takes minutes per 10 GB, so a file is hashed
    once: the match is recorded in a stamp beside it, and later calls trust
    the stamp while the file's size, mtime, and the manifest's checksum are
    the recorded ones. The container mounts the library writable, so a
    same-size swap that preserves the timestamp passes the stamp; ``verify``
    hashes regardless and is the remedy.

    A file whose manifest pins no checksum is judged against its own stamp,
    written at download: "unverified" without one, "recorded" while it
    matches, "changed since recorded" otherwise; "ready" is reserved for a
    file that matched its pin."""
    if path.is_symlink() and not path.exists():
        return "dangling symlink"
    if not path.exists():
        return "missing"
    if not path.is_file():
        return "not a regular file"
    path_stat = path.stat()
    if expected.size is not None and path_stat.st_size != expected.size:
        return "wrong size"
    reference = reference_sha256(path, expected)
    if reference is None:
        return "unverified"
    if verify or not is_stamped(path, path_stat, reference):
        if sha256_file(path) != reference:
            return "wrong checksum" if expected.sha256 is not None else "changed since recorded"
        write_stamp(path, reference)
    return "ready" if expected.sha256 is not None else "recorded"


def announce_hashing(located: list[tuple[Path, ModelFile]], verify: bool = False) -> None:
    """Say up front how many present files file_state is about to hash."""
    to_hash = []
    for path, model_file in located:
        if not path.is_file():
            continue
        reference = reference_sha256(path, model_file)
        if reference is not None and (verify or not is_stamped(path, path.stat(), reference)):
            to_hash.append(path.stat().st_size)
    if to_hash:
        total = sum(to_hash) / 1_000_000_000
        print(
            f"verifying: {len(to_hash)} file(s) ({total:.1f} GB) by checksum; "
            "later runs trust the record written beside each checked file"
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
    if model_file.civitai_version is not None:
        pinned = f"civitai\0{model_file.civitai_version}"
        pinned += "" if model_file.civitai_file is None else f"\0{model_file.civitai_file}"
    else:
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
    bytes beyond them need free space (none can be claimed for a file whose
    size is not pinned), and only an incomplete file needs its host."""

    model_file: ModelFile
    destination: Path
    staging: Path
    state: str
    received: int

    @property
    def remaining(self) -> int:
        return max(self.model_file.size - self.received, 0) if self.model_file.size is not None else 0


def pending_file(model_file: ModelFile, destination: Path) -> PendingFile:
    """The staged file is complete by construction (download renames it only
    once every byte is on disk), so a file without a pinned size is complete
    when anything is staged."""
    staging = staging_directory(model_file, destination)
    staged, part = staged_paths(model_file, staging)
    complete = staged_size(staged)
    if complete and model_file.size in (None, complete):
        return PendingFile(model_file, destination, staging, "complete", complete)
    # Without a pinned size nothing tells a resumed transfer from a changed
    # file spliced onto stale bytes, so an unpinned .part is started over.
    received = staged_size(part) if model_file.size is not None else 0
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
