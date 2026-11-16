"""The catalog and selection shared by the manifest kinds under config/
(model sets under config/model-sets, node sets under
config/custom-nodes/sets, workflow sets under config/workflow-sets) and
the rules every manifest kind shares: one reader, one relative-path rule,
one description rule, one digest pattern."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from pathlib import Path

from . import LatentCrateError

SET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# Every manifest kind carries this schema number.
SCHEMA = 1

# A lowercase hex SHA-256 and a lowercase full Git commit, as the manifests
# spell them; Git output is lower-cased before it is matched.
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
# A relative path a manifest names: below its own directory, no `.` or `..`
# segment, and only characters that survive a shell line unquoted, because
# a model-set `destination` and a node directory name reach one.
RELATIVE_PATH_RE = re.compile(r"^[A-Za-z0-9._+@-]+(/[A-Za-z0-9._+@-]+)*$")


def read_toml(path: Path, known_keys: set[str], context: str) -> dict:
    """A manifest's top-level table: readable, only known keys, schema 1.
    Every manifest kind words these three failures the same way."""
    try:
        table = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise LatentCrateError(f"{context}: cannot read the manifest ({error})") from error
    unknown = sorted(set(table) - known_keys)
    if unknown:
        raise LatentCrateError(f"{context}: unknown key(s): {', '.join(unknown)}")
    if table.get("schema") != SCHEMA:
        raise LatentCrateError(f"{context}: schema must be {SCHEMA}")
    return table


def description(table: dict, context: str) -> str:
    """The one-line description every manifest kind carries, stripped."""
    value = table.get("description")
    if not isinstance(value, str) or not value.strip() or "\n" in value.strip():
        raise LatentCrateError(f"{context}: description must be one non-empty line")
    return value.strip()


def relative_path(value: object, what: str, context: str) -> str:
    """A path below the manifest's own directory (see RELATIVE_PATH_RE)."""
    if not isinstance(value, str) or not RELATIVE_PATH_RE.fullmatch(value) or {".", ".."} & set(value.split("/")):
        raise LatentCrateError(f"{context}: {what} must be a relative path of letters, digits, and ._+@-: {value!r}")
    return value


def load_catalog(manifest_dir: Path, read_manifest: Callable[[Path], object], kind: str) -> dict:
    """Every manifest below manifest_dir, keyed by name, in set-name order:
    sorting file names would put `<name>-suffix` before `<name>`, because
    the dash sorts before the dot of the extension."""
    catalog: dict = {}
    for path in sorted(manifest_dir.glob("*.toml"), key=lambda item: item.stem):
        entry = read_manifest(path)
        catalog[entry.name] = entry
    if not catalog:
        raise LatentCrateError(f"no {kind}s found in {manifest_dir}")
    return catalog


def select_sets(catalog: dict, names: list[str], kind: str) -> list:
    """The catalog entries named on the command line (each once), or every
    entry for `all`."""
    if not names:
        raise LatentCrateError(f"provide one or more {kind} names, or all")
    if "all" in names:
        if len(names) != 1:
            raise LatentCrateError(f"all cannot be combined with named {kind}s")
        return list(catalog.values())
    selected: list = []
    seen: set[str] = set()
    for name in names:
        if name not in catalog:
            raise LatentCrateError(f"unknown {kind}: {name} (available: {', '.join(catalog)})")
        if name not in seen:
            selected.append(catalog[name])
            seen.add(name)
    return selected
