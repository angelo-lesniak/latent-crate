#!/usr/bin/env python3
"""Inspect the official ComfyUI templates installed in a LatentCrate image."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TemplateError(RuntimeError):
    """A safe, user-facing template error."""


@dataclass(frozen=True)
class Template:
    template_id: str
    title: str
    description: str
    media_type: str
    bundle: str
    custom_nodes: tuple[str, ...]
    workflow_path: Path


@dataclass(frozen=True)
class ModelHint:
    repository: str
    revision_hint: str
    source: str
    destination: str
    size: int
    sha256: str
    source_url: str


def read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TemplateError(f"could not read {label}: {error}") from error


def installed_assets() -> tuple[Path, dict[str, tuple[str, Path]], str]:
    try:
        from comfyui_workflow_templates_core import get_asset_path, iter_templates
    except ImportError as error:
        raise TemplateError(
            "the selected image does not contain the official ComfyUI template package"
        ) from error

    workflows: dict[str, tuple[str, Path]] = {}
    index_path: Path | None = None
    try:
        for entry in iter_templates():
            for asset in entry.assets:
                if asset.filename == "index.json":
                    index_path = Path(get_asset_path(entry.template_id, asset.filename))
                elif asset.filename.endswith(".json"):
                    workflows[entry.template_id] = (
                        entry.bundle,
                        Path(get_asset_path(entry.template_id, asset.filename)),
                    )
    except (FileNotFoundError, KeyError, OSError) as error:
        raise TemplateError(f"the installed template package is incomplete: {error}") from error

    if index_path is None:
        raise TemplateError("the installed template package has no English index.json")
    try:
        package_version = version("comfyui-workflow-templates")
    except PackageNotFoundError:
        package_version = "unknown"
    return index_path, workflows, package_version


def local_templates(
    index_document: object, workflows: dict[str, tuple[str, Path]]
) -> list[Template]:
    if not isinstance(index_document, list):
        raise TemplateError("the installed template index must be a JSON array")

    result: list[Template] = []
    seen: set[str] = set()
    for category in index_document:
        if not isinstance(category, dict):
            continue
        media_type = category.get("type", "other")
        if not isinstance(media_type, str) or not media_type:
            media_type = "other"
        entries = category.get("templates", [])
        if not isinstance(entries, list):
            continue
        for raw in entries:
            if not isinstance(raw, dict):
                continue
            template_id = raw.get("name")
            if not isinstance(template_id, str) or template_id in seen:
                continue
            installed = workflows.get(template_id)
            if installed is None:
                continue
            bundle, workflow_path = installed
            distributions = raw.get("includeOnDistributions")
            if raw.get("openSource") is not True:
                continue
            if raw.get("status", "active") != "active":
                continue
            if distributions is not None and (
                not isinstance(distributions, list) or "local" not in distributions
            ):
                continue
            # The package bundle is a stronger signal than a filename prefix.
            # A live /object_info check is still needed to prove every node is local.
            if bundle == "media-api":
                continue

            seen.add(template_id)
            title = raw.get("title", template_id)
            description = raw.get("description", title)
            if not isinstance(title, str) or not title.strip():
                title = template_id
            if not isinstance(description, str) or not description.strip():
                description = title
            custom_nodes = raw.get("requiresCustomNodes", [])
            if not isinstance(custom_nodes, list):
                custom_nodes = []
            result.append(
                Template(
                    template_id=template_id,
                    title=title.strip(),
                    description=description.strip(),
                    media_type=media_type,
                    bundle=bundle,
                    custom_nodes=tuple(
                        item for item in custom_nodes if isinstance(item, str) and item
                    ),
                    workflow_path=workflow_path,
                )
            )
    return sorted(result, key=lambda item: item.template_id.casefold())


def print_templates(templates: list[Template], package_version: str) -> None:
    if not templates:
        raise TemplateError("the selected image contains no local-compatible templates")
    rows = [
        (
            item.template_id,
            item.media_type,
            ", ".join(item.custom_nodes) if item.custom_nodes else "none",
            item.title,
        )
        for item in templates
    ]
    headers = ("TEMPLATE ID", "TYPE", "EXTRA NODES", "TITLE")
    widths = [max(len(headers[index]), *(len(row[index]) for row in rows)) for index in range(3)]
    print(
        f"{headers[0]:<{widths[0]}}  {headers[1]:<{widths[1]}}  "
        f"{headers[2]:<{widths[2]}}  {headers[3]}"
    )
    for row in rows:
        print(
            f"{row[0]:<{widths[0]}}  {row[1]:<{widths[1]}}  "
            f"{row[2]:<{widths[2]}}  {row[3]}"
        )
    print()
    print(
        f"{len(rows)} local-compatible template(s) from "
        f"comfyui-workflow-templates {package_version}."
    )
    print("This metadata check does not confirm that extra nodes or model files are installed.")


def safe_relative_path(value: str) -> str | None:
    if not value or "\\" in value or any(ord(character) < 32 for character in value):
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        return None
    return path.as_posix()


def iter_model_records(value: object):
    if isinstance(value, dict):
        models = value.get("models")
        if isinstance(models, list):
            for model in models:
                if isinstance(model, dict) and isinstance(model.get("url"), str):
                    yield model
        for nested in value.values():
            yield from iter_model_records(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from iter_model_records(nested)


def parse_hugging_face_hint(raw: dict) -> ModelHint | None:
    url = raw["url"]
    if any(ord(character) < 32 for character in url):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "huggingface.co":
        return None
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 6 or parts[2] != "resolve":
        return None
    repository = f"{parts[0]}/{parts[1]}"
    revision_hint = parts[3]
    if not revision_hint or any(ord(character) < 32 for character in revision_hint):
        return None
    source = safe_relative_path("/".join(parts[4:]))
    if REPOSITORY_RE.fullmatch(repository) is None or source is None:
        return None

    raw_name = raw.get("name")
    filename = raw_name if isinstance(raw_name, str) else PurePosixPath(source).name
    if safe_relative_path(filename) != filename or "/" in filename:
        filename = PurePosixPath(source).name
    raw_directory = raw.get("directory")
    directory = safe_relative_path(raw_directory) if isinstance(raw_directory, str) else None
    destination = f"{directory}/{filename}" if directory else f"TODO/{filename}"

    raw_size = raw.get("size")
    size = raw_size if isinstance(raw_size, int) and not isinstance(raw_size, bool) and raw_size > 0 else 0
    raw_hash = raw.get("hash")
    digest = raw_hash.lower() if isinstance(raw_hash, str) else ""
    if SHA256_RE.fullmatch(digest) is None:
        digest = ""
    return ModelHint(repository, revision_hint, source, destination, size, digest, url)


def model_hints(workflow: object) -> tuple[list[ModelHint], list[str]]:
    hints: dict[str, ModelHint] = {}
    unsupported: set[str] = set()
    for raw in iter_model_records(workflow):
        hint = parse_hugging_face_hint(raw)
        if hint is None:
            unsupported.add(raw["url"])
            continue
        previous = hints.get(hint.destination)
        if previous is None:
            hints[hint.destination] = hint
            continue
        if (
            previous.repository,
            previous.revision_hint,
            previous.source,
        ) != (hint.repository, hint.revision_hint, hint.source):
            raise TemplateError(f"the template gives conflicting model hints for {hint.destination}")
        if previous.size and hint.size and previous.size != hint.size:
            raise TemplateError(f"the template gives conflicting sizes for {hint.destination}")
        if previous.sha256 and hint.sha256 and previous.sha256 != hint.sha256:
            raise TemplateError(f"the template gives conflicting checksums for {hint.destination}")
        hints[hint.destination] = ModelHint(
            previous.repository,
            previous.revision_hint,
            previous.source,
            previous.destination,
            previous.size or hint.size,
            previous.sha256 or hint.sha256,
            previous.source_url,
        )
    return sorted(hints.values(), key=lambda item: item.destination), sorted(unsupported)


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def comment_text(value: str) -> str:
    return "".join(character if 32 <= ord(character) != 127 else "?" for character in value)


def render_draft(
    template: Template,
    package_version: str,
    hints: list[ModelHint],
    unsupported_urls: list[str],
) -> str:
    if not hints:
        raise TemplateError(
            "this template has no embedded Hugging Face model hints; "
            "LatentCrate model sets currently support Hugging Face files only"
        )
    workflow_url = (
        "https://github.com/Comfy-Org/workflow_templates/blob/"
        f"REPLACE_WITH_40_CHARACTER_COMMIT/templates/{template.template_id}.json"
    )
    lines = [
        "# DRAFT: models fetch will reject this file until every TODO is completed.",
        f"# Source: comfyui-workflow-templates {package_version}, template {template.template_id}",
        "# Confirm repositories, licenses, destinations, byte sizes, and SHA-256 values.",
        "",
        "schema = 1",
        f"description = {toml_string(f'Models for {template.title}')}",
        f"workflow_urls = [{toml_string(workflow_url)}] # TODO: pin the official workflow commit",
        "",
        "# TODO: Add one or more immutable model-license links.",
        "# [[license]]",
        '# name = "Model license"',
        '# url = "https://huggingface.co/owner/repository/blob/<40-character-commit>/LICENSE"',
    ]
    for hint in hints:
        revision = hint.revision_hint if COMMIT_RE.fullmatch(hint.revision_hint) else ""
        lines.extend(
            [
                "",
                f"# Embedded source: {comment_text(hint.source_url)}",
                "[[file]]",
                f"repository = {toml_string(hint.repository)}",
                f"revision = {toml_string(revision)}"
                + (
                    ""
                    if revision
                    else " # TODO: replace the embedded "
                    f"{comment_text(hint.revision_hint)!r} reference with a commit"
                ),
                f"source = {toml_string(hint.source)}",
                f"destination = {toml_string(hint.destination)}"
                + (" # TODO: choose the ComfyUI model directory" if hint.destination.startswith("TODO/") else ""),
                f"size = {hint.size}" + (" # TODO: exact byte size" if not hint.size else ""),
                f"sha256 = {toml_string(hint.sha256)}"
                + (" # TODO: lowercase SHA-256" if not hint.sha256 else ""),
            ]
        )
    if unsupported_urls:
        lines.extend(["", "# URLs not supported by the current Hugging Face-only model-set format:"])
        lines.extend(f"# TODO: {comment_text(url)}" for url in unsupported_urls)
    lines.append("")
    return "\n".join(lines)


def write_new_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            descriptor = None
            output.write(content)
    except FileExistsError as error:
        raise TemplateError(f"draft already exists and was not overwritten: {path.name}") from error
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("list", help="list local-compatible official templates")
    create = subparsers.add_parser(
        "create-model-set", help="create a reviewable model-set manifest draft"
    )
    create.add_argument("template")
    create.add_argument("--name")
    create.add_argument("--output-dir", type=Path, default=Path("/output"))
    return parser.parse_args()


def main() -> int:
    # Linux containers normally use UTF-8. Keep diagnostics usable if a
    # provider attaches a terminal with a narrower encoding.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")
    args = parse_args()
    try:
        index_path, workflows, package_version = installed_assets()
        templates = local_templates(read_json(index_path, "the installed template index"), workflows)
        if args.action == "list":
            print_templates(templates, package_version)
            return 0

        selected = next((item for item in templates if item.template_id == args.template), None)
        if selected is None:
            raise TemplateError(
                f"unknown or non-local template: {args.template} "
                "(run: bash bin/latentcrate templates list)"
            )
        name = args.name or selected.template_id
        if NAME_RE.fullmatch(name) is None or name == "all":
            raise TemplateError(f"unsafe model-set name: {name}")
        workflow = read_json(selected.workflow_path, f"template {selected.template_id}")
        hints, unsupported_urls = model_hints(workflow)
        draft = render_draft(selected, package_version, hints, unsupported_urls)
        output = args.output_dir / f"{name}.toml"
        write_new_file(output, draft)
        print(f"Created model-set draft: build/model-set-drafts/{name}.toml")
        print("Complete every TODO, then move it to config/model-sets/ and run the model-set checks.")
        return 0
    except TemplateError as error:
        print(f"LatentCrate: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"LatentCrate: filesystem operation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
