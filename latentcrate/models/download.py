"""Download the selected model sets into the library and report what is ready."""

from __future__ import annotations

import http.client
import os
import urllib.error
from pathlib import Path

from .. import LatentCrateError
from .hub import check_access, file_url, open_url
from .library import (
    PendingFile,
    announce_hashing,
    destination_path,
    file_state,
    is_within,
    pending_file,
    prepare_staging_directory,
    remove_staging_directory,
    require_free_space,
    sha256_file,
    staged_paths,
    staging_directory,
    write_stamp,
)
from .manifest import ModelSet, merged_files

CHUNK_SIZE = 8 * 1024 * 1024


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


def download(pending: PendingFile, token: str | None) -> Path:
    """Stream the file into staging, resuming a partial download, and return the staged path.

    The stream goes to <name>.part; a previous .part file is resumed with a
    Range request (206), restarted when the server ignores the range (200),
    and taken as complete when the server has no byte beyond it (416). The
    .part file is renamed only once every byte the manifest promises is on
    disk, so anything else in the staging directory is a complete download
    awaiting verification, which is returned without a request."""
    model_file = pending.model_file
    staged, part = staged_paths(model_file, pending.staging)
    if pending.state == "complete":
        return staged
    received = pending.received
    headers = {"Range": f"bytes={received}-"} if received else {}
    report_step = max(model_file.size // 10, CHUNK_SIZE)
    next_report = report_step
    try:
        with open_url(file_url(model_file), headers, token) as response:
            if response.status == 206:
                content_range = response.headers.get("Content-Range", "")
                if not content_range.startswith(f"bytes {received}-"):
                    raise LatentCrateError(
                        f"{model_file.destination}: the server resumed at an unexpected offset ({content_range})"
                    )
                total = content_range.rpartition("/")[2]
            elif response.status == 200:
                received = 0
                total = response.headers.get("Content-Length", "")
            else:
                raise LatentCrateError(f"{model_file.destination}: unexpected HTTP status {response.status}")
            if total.isdigit() and int(total) != model_file.size:
                raise LatentCrateError(
                    f"{model_file.destination}: the server reports {total} bytes "
                    f"but the manifest pins {model_file.size}"
                )
            with part.open("r+b" if received else "wb") as handle:
                handle.seek(received)
                for chunk in iter(lambda: response.read(CHUNK_SIZE), b""):
                    handle.write(chunk)
                    received += len(chunk)
                    if received >= next_report:
                        print(f"  {received / 1_000_000_000:.1f} of {model_file.size / 1_000_000_000:.1f} GB")
                        next_report += report_step
                handle.flush()
                os.fsync(handle.fileno())
    except urllib.error.HTTPError as error:
        if error.code == 416 and received:
            os.replace(part, staged)
            return staged
        raise LatentCrateError(f"download failed for {model_file.destination}: HTTP {error.code}") from error
    except (OSError, http.client.HTTPException) as error:
        raise LatentCrateError(
            f"download of {model_file.destination} stopped after {received} of {model_file.size} bytes ({error}); "
            "run models fetch again to resume"
        ) from error
    if received < model_file.size:
        raise LatentCrateError(
            f"download of {model_file.destination} stopped after {received} of {model_file.size} bytes; "
            "run models fetch again to resume"
        )
    os.replace(part, staged)
    return staged


def fetch(selected: list[ModelSet], models_root: Path, roots: tuple[Path, ...], token: str | None) -> None:
    files = merged_files(selected)
    print_notices(selected)

    located = [(destination_path(models_root, model_file.destination, roots), model_file) for model_file in files]
    announce_hashing(located)
    pending: list[PendingFile] = []
    for destination, model_file in located:
        state = file_state(destination, model_file)
        if state == "ready":
            # A leftover of an earlier run that published the file.
            staging = staging_directory(model_file, destination)
            if staging.is_dir():
                remove_staging_directory(staging, staging.parent)
            print(f"ready: {model_file.destination}")
        else:
            if state != "missing":
                raise LatentCrateError(
                    f"{model_file.destination} has the {state}; move it aside before downloading the pinned file"
                )
            pending.append(pending_file(model_file, destination))
    if not pending:
        print("All selected model files are ready.")
        return

    require_free_space(models_root, pending)
    incomplete = [item for item in pending if item.state != "complete"]
    if incomplete:
        print("Checking access to every selected Hugging Face file before downloading ...")
        for item in incomplete:
            check_access(item.model_file, token)

    for item in pending:
        model_file, destination, staging = item.model_file, item.destination, item.staging
        staging_root = staging.parent
        destination.parent.mkdir(parents=True, exist_ok=True)
        # The walk of the staging root covers this file's staging directory too.
        prepare_staging_directory(staging_root)
        staging.mkdir(mode=0o700, exist_ok=True)
        if not is_within(staging.resolve(strict=True), roots):
            raise LatentCrateError(f"staging directory escapes the allowed model storage: {model_file.destination}")
        if item.state == "complete":
            print(f"verifying: {model_file.destination}")
        else:
            print(f"downloading: {model_file.destination} ({model_file.size / 1_000_000_000:.1f} GB)")
        downloaded = download(item, token)
        # A complete download that is not the pinned file is discarded. A
        # verified one is published as a hard link, which the kernel refuses
        # when any entry holds the destination, so nothing that appeared
        # meanwhile is overwritten; when publishing fails, the file stays in
        # staging and the next run publishes it without downloading again.
        if downloaded.stat().st_size != model_file.size:
            remove_staging_directory(staging, staging_root)
            raise LatentCrateError(f"downloaded size does not match the manifest: {model_file.destination}")
        if sha256_file(downloaded) != model_file.sha256:
            remove_staging_directory(staging, staging_root)
            raise LatentCrateError(f"downloaded checksum does not match the manifest: {model_file.destination}")
        try:
            os.link(downloaded, destination)
        except FileExistsError:
            if file_state(destination, model_file) != "ready":
                raise LatentCrateError(
                    f"{model_file.destination} appeared while downloading and was not overwritten; review it and retry"
                ) from None
        else:
            write_stamp(destination, model_file.sha256)
        remove_staging_directory(staging, staging_root)
        print(f"installed: {model_file.destination}")
    print("Selected model sets are ready. Start ComfyUI with: bash bin/latentcrate up")


def status(selected: list[ModelSet], models_root: Path, roots: tuple[Path, ...], *, verify: bool = False) -> int:
    files = merged_files(selected)
    located = [(destination_path(models_root, model_file.destination, roots), model_file) for model_file in files]
    announce_hashing(located, verify)
    result = 0
    for path, model_file in located:
        state = file_state(path, model_file, verify=verify)
        print(f"{model_file.destination}: {state}")
        if state != "ready":
            result = 1
    return result
