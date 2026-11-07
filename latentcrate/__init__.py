"""LatentCrate: pinned ComfyUI containers for Docker and Podman.

The package is the whole command-line tool, run as ``python3 -m
latentcrate``; every module raises :class:`LatentCrateError` for a
user-facing failure, which the dispatcher prints as ``LatentCrate:
<message>`` and exits 1.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The repository root: the package lives directly below it.
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LatentCrateError(Exception):
    """A safe, user-facing error message. The dispatcher reports an OSError
    the same way, as a failed filesystem operation."""


def warn(message: str) -> None:
    """Print a one-line notice on stderr in the wrapper's voice."""
    print(f"LatentCrate: {message}", file=sys.stderr, flush=True)
