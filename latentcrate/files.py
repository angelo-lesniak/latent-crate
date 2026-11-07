"""Write a file atomically: the whole text appears under its name or nothing does."""

from __future__ import annotations

import os
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
