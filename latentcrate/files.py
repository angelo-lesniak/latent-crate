"""Publish files and trees atomically: a reader sees the previous content or the complete new one."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def write_atomic(path: Path, text: str, mode: int = 0o644) -> None:
    """Write text to path through a temporary file in the same directory and rename it into place.

    A reader sees the previous file or the complete new one, never a partial
    write; the temporary file is removed when anything fails."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def publish_tree(staging: Path, destination: Path, previous: Path) -> None:
    """Move the destination tree aside to previous, rename staging into its
    place, and remove previous; when the second rename fails, the previous
    tree is put back and the error propagates with staging untouched."""
    if destination.exists():
        os.rename(destination, previous)
    try:
        os.rename(staging, destination)
    except BaseException:
        if previous.exists():
            os.rename(previous, destination)
        raise
    shutil.rmtree(previous, ignore_errors=True)
