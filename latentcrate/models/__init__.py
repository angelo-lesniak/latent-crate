"""Fetch, verify, check, and draft the Hugging Face and Civitai files that model-set manifests declare.

The CLI runs this package in-process on the host: ``models fetch`` calls
:func:`fetch` inside :func:`model_download_lock`, ``models status`` calls
:func:`status`, ``models check`` calls :func:`check`, and ``models draft``
prints the text :func:`draft` returns. The tokens arrive as a plain
Python argument, :class:`hub.Tokens` (the CLI captures ``HF_TOKEN`` and
``CIVITAI_TOKEN`` in-process), so they never travel through an
environment or a command line, and :func:`hub.open_url` sends each to its
provider's host only.

The modules layer upward: :mod:`manifest` reads the TOML, :mod:`library`
keeps the on-disk model library, :mod:`hub` talks to Hugging Face and Civitai,
:mod:`download` fetches and reports, and :mod:`drafts` serves manifest
authors.
"""

from __future__ import annotations

from .download import fetch, status
from .drafts import check, draft
from .hub import Tokens
from .library import allowed_roots, model_download_lock
from .manifest import License, ModelFile, ModelSet, catalog, merged_files, read_manifest

__all__ = [
    "License",
    "ModelFile",
    "ModelSet",
    "Tokens",
    "allowed_roots",
    "catalog",
    "check",
    "draft",
    "fetch",
    "merged_files",
    "model_download_lock",
    "read_manifest",
    "status",
]
