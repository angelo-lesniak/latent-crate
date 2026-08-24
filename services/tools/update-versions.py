#!/usr/bin/env python3
"""Resolve the latest eligible versions for a LatentCrate version profile."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, NoReturn


MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_API_PAGES = 20
API_PAGE_SIZE = 100
USER_AGENT = "LatentCrate-version-update"
FRONTEND_DIGEST_COMMAND = (
    "python",
    "/usr/local/bin/latentcrate-frontend-release.py",
    "digest",
)
UPDATE_PREFIX = "LATENTCRATE_VERSION_UPDATE"
RESULT_PREFIX = "LATENTCRATE_VERSION_RESULT"
STABLE_TAG_RE = r"(\d+(?:\.\d+)+)"


class VersionUpdateError(RuntimeError):
    """A source could not produce one safe update candidate."""


@dataclass(frozen=True)
class Resolution:
    component: str
    current: str
    latest: str
    updates: tuple[tuple[str, str], ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class GitHubSource:
    key: str
    owner: str
    repository: str
    tag_pattern: re.Pattern[str]
    frontend: bool = False


COMPONENTS = (
    "comfyui",
    "frontend",
    "ffmpeg",
    "nv-codec-headers",
    "svt-av1",
    "sageattention",
    "pytorch",
    "node",
    "pnpm",
    "tool-python",
    "torchcodec",
)

GITHUB_SOURCES = {
    "comfyui": GitHubSource(
        "COMFYUI_REF",
        "comfyanonymous",
        "ComfyUI",
        re.compile(rf"^v{STABLE_TAG_RE}$"),
    ),
    "frontend": GitHubSource(
        "COMFYUI_FRONTEND_REF",
        "Comfy-Org",
        "ComfyUI_frontend",
        re.compile(rf"^v{STABLE_TAG_RE}$"),
        frontend=True,
    ),
    "ffmpeg": GitHubSource(
        "FFMPEG_REF",
        "FFmpeg",
        "FFmpeg",
        re.compile(rf"^n{STABLE_TAG_RE}$"),
    ),
    "nv-codec-headers": GitHubSource(
        "NV_CODEC_HEADERS_REF",
        "FFmpeg",
        "nv-codec-headers",
        re.compile(rf"^n{STABLE_TAG_RE}$"),
    ),
    "sageattention": GitHubSource(
        "SAGEATTENTION_REF",
        "thu-ml",
        "SageAttention",
        re.compile(rf"^v{STABLE_TAG_RE}$"),
    ),
}


def fail(message: str) -> NoReturn:
    raise SystemExit(f"LatentCrate: {message}")


def clean_text(value: object) -> str:
    printable = "".join(
        character
        for character in str(value)
        if character.isprintable() or character.isspace()
    )
    return " ".join(printable.split())


def request_bytes(url: str, *, accept: str = "application/json") -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": accept, "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            if urllib.parse.urlsplit(response.url).scheme.lower() != "https":
                raise VersionUpdateError(f"version source redirected away from HTTPS: {url}")
            announced = response.headers.get("Content-Length")
            if announced and announced.isdigit() and int(announced) > MAX_RESPONSE_BYTES:
                raise VersionUpdateError(f"version source response is too large: {url}")
            content = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.URLError) as error:
        raise VersionUpdateError(f"could not read version source {url}: {error}") from error
    if len(content) > MAX_RESPONSE_BYTES:
        raise VersionUpdateError(f"version source response is too large: {url}")
    return content


def request_json(url: str, *, accept: str = "application/json") -> Any:
    content = request_bytes(url, accept=accept)
    try:
        return json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VersionUpdateError(f"version source returned invalid JSON: {url}") from error


def paginated_list(
    url: str,
    source: str,
    *,
    accept: str = "application/json",
) -> list[object]:
    items: list[object] = []
    separator = "&" if "?" in url else "?"
    for page in range(1, MAX_API_PAGES + 1):
        payload = request_json(
            f"{url}{separator}per_page={API_PAGE_SIZE}&page={page}",
            accept=accept,
        )
        if not isinstance(payload, list):
            raise VersionUpdateError(f"{source} returned an invalid paginated response")
        items.extend(payload)
        if len(payload) < API_PAGE_SIZE:
            return items
    raise VersionUpdateError(f"{source} exceeded the {MAX_API_PAGES}-page safety limit")


def version_key(value: str, pattern: re.Pattern[str]) -> tuple[int, ...] | None:
    match = pattern.fullmatch(value)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def latest_tag(tags: list[str], pattern: re.Pattern[str], source: str) -> str:
    candidates = [
        (parsed, tag)
        for tag in tags
        if (parsed := version_key(tag, pattern)) is not None
    ]
    if not candidates:
        raise VersionUpdateError(f"{source} returned no stable version tags")
    return max(candidates)[1]


def current_is_latest(
    current: tuple[int, ...], latest: tuple[int, ...], source: str
) -> bool:
    if latest < current:
        raise VersionUpdateError(
            f"{source} latest eligible version is older than the current pin"
        )
    return latest == current


def github_tags(source: GitHubSource) -> list[str]:
    payload = paginated_list(
        f"https://api.github.com/repos/{source.owner}/{source.repository}/tags",
        f"GitHub tags for {source.owner}/{source.repository}",
        accept="application/vnd.github+json",
    )
    return [item.get("name", "") for item in payload if isinstance(item, dict)]


def github_release_notes(
    source: GitHubSource, current: str, latest: str
) -> tuple[str, ...]:
    url = f"https://api.github.com/repos/{source.owner}/{source.repository}/releases"
    fallback = f"https://github.com/{source.owner}/{source.repository}/releases"
    try:
        payload = paginated_list(
            url,
            f"GitHub releases for {source.owner}/{source.repository}",
            accept="application/vnd.github+json",
        )
    except VersionUpdateError:
        return (f"  Release history: {fallback}",)
    if not isinstance(payload, list):
        return (f"  Release history: {fallback}",)
    current_key = version_key(current, source.tag_pattern)
    latest_key = version_key(latest, source.tag_pattern)
    if current_key is None or latest_key is None:
        return (f"  Release history: {fallback}",)
    releases: list[tuple[tuple[int, ...], str]] = []
    for item in payload:
        if not isinstance(item, dict) or item.get("draft") or item.get("prerelease"):
            continue
        tag = item.get("tag_name", "")
        parsed = version_key(tag, source.tag_pattern)
        if parsed is None or not (current_key < parsed <= latest_key):
            continue
        title = clean_text(item.get("name") or tag) or tag
        link = clean_text(item.get("html_url") or fallback) or fallback
        releases.append((parsed, f"  {title}: {link}"))
    if not releases:
        return (f"  Release history: {fallback}",)
    return tuple(line for _, line in sorted(releases))


def frontend_digest(reference: str) -> str:
    completed = subprocess.run(
        (*FRONTEND_DIGEST_COMMAND, reference),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = clean_text(completed.stderr) or "frontend digest helper failed"
        raise VersionUpdateError(detail)
    matches = re.findall(
        r"^COMFY_FRONTEND_DIST_SHA256=([0-9a-f]{64})$",
        completed.stdout,
        re.MULTILINE,
    )
    if len(matches) != 1:
        raise VersionUpdateError("frontend digest helper returned invalid output")
    return matches[0]


def resolve_github(component: str, values: dict[str, str]) -> Resolution:
    source = GITHUB_SOURCES[component]
    current_value = required_value(values, source.key)
    current_tag = current_value
    if source.frontend:
        expected_prefix = f"{source.owner}/{source.repository}@"
        if not current_value.startswith(expected_prefix):
            raise VersionUpdateError(
                f"{source.key} must use {expected_prefix}TAG"
            )
        current_tag = current_value.removeprefix(expected_prefix)
    current_key = version_key(current_tag, source.tag_pattern)
    if current_key is None:
        raise VersionUpdateError(f"unsupported current {source.key}: {current_value}")
    latest = latest_tag(
        github_tags(source), source.tag_pattern, f"{source.owner}/{source.repository}"
    )
    latest_key = version_key(latest, source.tag_pattern)
    same_version = current_is_latest(
        current_key,
        latest_key,
        f"GitHub {source.owner}/{source.repository}",
    )
    if source.frontend:
        reference = f"{source.owner}/{source.repository}@{latest}"
        digest = frontend_digest(reference)
        current_digest = values.get("COMFY_FRONTEND_DIST_SHA256", "")
        if same_version:
            if digest == current_digest:
                return Resolution(component, current_value, current_value, ())
            if current_digest:
                raise VersionUpdateError(
                    "frontend archive digest changed for the unchanged release "
                    f"{reference}; investigate the replaced asset or explicitly "
                    "accept it with frontend pin-release"
                )
        notes = () if same_version else github_release_notes(source, current_tag, latest)
        return Resolution(
            component,
            current_value,
            reference if not same_version else f"{reference} (archive digest populated)",
            (
                (source.key, reference),
                ("COMFY_FRONTEND_DIST_SHA256", digest),
            ),
            notes,
        )
    if same_version:
        return Resolution(component, current_value, current_value, ())
    notes = github_release_notes(source, current_tag, latest)
    return Resolution(
        component,
        current_value,
        latest,
        ((source.key, latest),),
        notes,
    )


def resolve_svt_av1(values: dict[str, str]) -> Resolution:
    component = "svt-av1"
    key = "SVT_AV1_REF"
    pattern = re.compile(rf"^v{STABLE_TAG_RE}$")
    current = required_value(values, key)
    current_key = version_key(current, pattern)
    if current_key is None:
        raise VersionUpdateError(f"unsupported current {key}: {current}")
    project = urllib.parse.quote("AOMediaCodec/SVT-AV1", safe="")
    url = f"https://gitlab.com/api/v4/projects/{project}/releases"
    payload = paginated_list(url, "GitLab SVT-AV1 releases")
    releases: list[tuple[tuple[int, ...], str, str]] = []
    for item in payload:
        if not isinstance(item, dict) or item.get("upcoming_release"):
            continue
        tag = item.get("tag_name", "")
        parsed = version_key(tag, pattern)
        if parsed is None:
            continue
        title = clean_text(item.get("name") or tag) or tag
        link = (
            "https://gitlab.com/AOMediaCodec/SVT-AV1/-/releases/"
            f"{urllib.parse.quote(tag, safe='')}"
        )
        releases.append((parsed, tag, f"  {title}: {link}"))
    if not releases:
        raise VersionUpdateError("GitLab returned no stable SVT-AV1 releases")
    latest_key, latest, _ = max(releases)
    if current_is_latest(current_key, latest_key, "GitLab SVT-AV1"):
        return Resolution(component, current, current, ())
    notes = tuple(
        note for parsed, _, note in sorted(releases) if current_key < parsed <= latest_key
    )
    return Resolution(component, current, latest, ((key, latest),), notes)


PACKAGE_VERSION_RE = re.compile(rf"^{STABLE_TAG_RE}$")
TORCHCODEC_VERSION_RE = re.compile(rf"^{STABLE_TAG_RE}(?:\+([A-Za-z0-9.]+))?$")


def resolve_pnpm(values: dict[str, str]) -> Resolution:
    component = "pnpm"
    key = "FRONTEND_PNPM_VERSION"
    current = required_value(values, key)
    current_key = version_key(current, PACKAGE_VERSION_RE)
    if current_key is None:
        raise VersionUpdateError(f"unsupported current {key}: {current}")
    payload = request_json("https://registry.npmjs.org/pnpm/latest")
    latest = payload.get("version", "") if isinstance(payload, dict) else ""
    latest_key = version_key(latest, PACKAGE_VERSION_RE)
    if latest_key is None:
        raise VersionUpdateError("npm returned no stable pnpm latest version")
    if current_is_latest(current_key, latest_key, "npm pnpm"):
        return Resolution(component, current, current, ())
    return Resolution(
        component,
        current,
        latest,
        ((key, latest),),
        ("  Package history: https://www.npmjs.com/package/pnpm?activeTab=versions",),
    )


def torchcodec_versions(payload: object) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    found: set[str] = set()
    for item in payload.get("files", []):
        if not isinstance(item, dict):
            continue
        if item.get("yanked", False) is not False:
            continue
        filename = urllib.parse.unquote(str(item.get("filename", "")))
        match = re.match(r"^torchcodec-([^-]+)-", filename, re.IGNORECASE)
        if match:
            found.add(match.group(1))
    return found


class SimpleIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.files: list[tuple[str, bool]] = []

    def handle_starttag(
        self, tag: str, attributes: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() != "a":
            return
        href = next(
            (value for name, value in attributes if name.lower() == "href" and value),
            None,
        )
        if href:
            yanked = any(name.lower() == "data-yanked" for name, _ in attributes)
            self.files.append((href, yanked))


def torchcodec_index_versions(content: bytes) -> set[str]:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise VersionUpdateError("the configured package index returned invalid content") from error
        parser = SimpleIndexParser()
        parser.feed(text)
        payload = {
            "files": [
                {
                    "filename": urllib.parse.urlsplit(href).path.rsplit("/", 1)[-1],
                    "yanked": yanked,
                }
                for href, yanked in parser.files
            ]
        }
    return torchcodec_versions(payload)


def resolve_torchcodec(values: dict[str, str]) -> Resolution:
    component = "torchcodec"
    key = "TORCHCODEC_VERSION"
    current = values.get(key, "")
    if not current:
        return Resolution(component, "disabled", "disabled", ())
    current_match = TORCHCODEC_VERSION_RE.fullmatch(current)
    if current_match is None:
        raise VersionUpdateError(f"unsupported current {key}: {current}")
    suffix = current_match.group(2)
    index = required_value(values, "TORCHCODEC_INDEX_URL").rstrip("/")
    content = request_bytes(
        f"{index}/torchcodec/",
        accept="application/vnd.pypi.simple.v1+json",
    )
    candidates: list[tuple[tuple[int, ...], str]] = []
    for version in torchcodec_index_versions(content):
        match = TORCHCODEC_VERSION_RE.fullmatch(version)
        if match is None or match.group(2) != suffix:
            continue
        parsed = tuple(int(part) for part in match.group(1).split("."))
        candidates.append((parsed, version))
    if not candidates:
        raise VersionUpdateError("the configured package index returned no compatible TorchCodec version")
    latest_key, latest = max(candidates)
    current_key = tuple(int(part) for part in current_match.group(1).split("."))
    if current_is_latest(current_key, latest_key, "the configured TorchCodec index"):
        return Resolution(component, current, current, ())
    return Resolution(
        component,
        current,
        latest,
        ((key, latest),),
        (f"  Package index: {index}/torchcodec/",),
    )


def parse_image(value: str, key: str) -> tuple[str, str, str, str]:
    match = re.fullmatch(
        r"docker\.io/(?P<namespace>[a-z0-9._-]+)/(?P<repository>[a-z0-9._-]+):(?P<tag>[A-Za-z0-9._-]+)",
        value,
    )
    if match is None:
        raise VersionUpdateError(f"unsupported current {key}: {value}")
    return (
        f"docker.io/{match['namespace']}/{match['repository']}",
        match["namespace"],
        match["repository"],
        match["tag"],
    )


def docker_hub_tags(namespace: str, repository: str, search: str) -> list[str]:
    query = urllib.parse.urlencode({"page_size": 100, "name": search})
    url: str | None = (
        f"https://hub.docker.com/v2/namespaces/{namespace}/repositories/"
        f"{repository}/tags?{query}"
    )
    tags: list[str] = []
    pages = 0
    while url:
        if pages >= MAX_API_PAGES:
            raise VersionUpdateError(
                f"Docker Hub tags for {namespace}/{repository} exceeded the "
                f"{MAX_API_PAGES}-page safety limit"
            )
        payload = request_json(url)
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise VersionUpdateError(f"Docker Hub returned invalid tags for {namespace}/{repository}")
        tags.extend(
            str(item.get("name", ""))
            for item in payload["results"]
            if isinstance(item, dict)
        )
        next_url = payload.get("next")
        if next_url:
            next_url = str(next_url)
            parsed_next = urllib.parse.urlsplit(next_url)
            if parsed_next.scheme != "https" or parsed_next.hostname != "hub.docker.com":
                raise VersionUpdateError("Docker Hub returned an unsafe pagination link")
            url = next_url
        else:
            url = None
        pages += 1
    return tags


def resolve_simple_image(
    component: str,
    key: str,
    values: dict[str, str],
    pattern: re.Pattern[str],
    search: str,
) -> Resolution:
    current = required_value(values, key)
    image, namespace, repository, current_tag = parse_image(current, key)
    current_key = version_key(current_tag, pattern)
    if current_key is None:
        raise VersionUpdateError(f"unsupported current {key}: {current}")
    candidates = [
        tag
        for tag in docker_hub_tags(namespace, repository, search)
        if (parsed := version_key(tag, pattern)) is not None
        and len(parsed) == len(current_key)
    ]
    latest = latest_tag(candidates, pattern, f"Docker Hub {namespace}/{repository}")
    latest_key = version_key(latest, pattern)
    if current_is_latest(
        current_key,
        latest_key,
        f"Docker Hub {namespace}/{repository}",
    ):
        return Resolution(component, current, current, ())
    candidate = f"{image}:{latest}"
    image_history = (
        f"https://hub.docker.com/_/{repository}"
        if namespace == "library"
        else f"https://hub.docker.com/r/{namespace}/{repository}/tags"
    )
    return Resolution(
        component,
        current,
        candidate,
        ((key, candidate),),
        (f"  Image tags: {image_history}",),
    )


PYTORCH_TAG_RE = re.compile(
    rf"^{STABLE_TAG_RE}-cuda(\d+(?:\.\d+)+)-cudnn(\d+)-(devel|runtime)$"
)


def resolve_pytorch(values: dict[str, str]) -> Resolution:
    component = "pytorch"
    devel_key = "PYTORCH_DEVEL_IMAGE"
    runtime_key = "PYTORCH_RUNTIME_IMAGE"
    devel = required_value(values, devel_key)
    runtime = required_value(values, runtime_key)
    image, namespace, repository, devel_tag = parse_image(devel, devel_key)
    runtime_image, runtime_namespace, runtime_repository, runtime_tag = parse_image(
        runtime, runtime_key
    )
    if (image, namespace, repository) != (
        runtime_image,
        runtime_namespace,
        runtime_repository,
    ):
        raise VersionUpdateError("PyTorch development and runtime images must use one repository")
    devel_match = PYTORCH_TAG_RE.fullmatch(devel_tag)
    runtime_match = PYTORCH_TAG_RE.fullmatch(runtime_tag)
    if (
        devel_match is None
        or runtime_match is None
        or devel_match.group(4) != "devel"
        or runtime_match.group(4) != "runtime"
        or devel_match.groups()[:3] != runtime_match.groups()[:3]
    ):
        raise VersionUpdateError("PyTorch image pins must be a matching devel/runtime pair")
    cuda = devel_match.group(2)
    cudnn = devel_match.group(3)
    tags = docker_hub_tags(namespace, repository, f"cuda{cuda}-cudnn{cudnn}")
    pairs: dict[tuple[int, ...], dict[str, str]] = {}
    for tag in tags:
        match = PYTORCH_TAG_RE.fullmatch(tag)
        if match is None or match.group(2) != cuda or match.group(3) != cudnn:
            continue
        parsed = tuple(int(part) for part in match.group(1).split("."))
        pairs.setdefault(parsed, {})[match.group(4)] = tag
    complete = [(parsed, pair) for parsed, pair in pairs.items() if set(pair) == {"devel", "runtime"}]
    if not complete:
        raise VersionUpdateError("Docker Hub returned no matching PyTorch devel/runtime image pair")
    latest_key, latest_pair = max(complete)
    current_key = tuple(int(part) for part in devel_match.group(1).split("."))
    current_display = f"{devel_match.group(1)} / CUDA {cuda} / cuDNN {cudnn}"
    if current_is_latest(current_key, latest_key, "Docker Hub PyTorch"):
        return Resolution(component, current_display, current_display, ())
    latest_display = f"{'.'.join(str(part) for part in latest_key)} / CUDA {cuda} / cuDNN {cudnn}"
    return Resolution(
        component,
        current_display,
        latest_display,
        (
            (devel_key, f"{image}:{latest_pair['devel']}"),
            (runtime_key, f"{image}:{latest_pair['runtime']}"),
        ),
        (f"  Image tags: https://hub.docker.com/r/{namespace}/{repository}/tags",),
    )


def required_value(values: dict[str, str], key: str) -> str:
    value = values.get(key, "")
    if not value:
        raise VersionUpdateError(f"version profile has no value for {key}")
    return value


def resolve_component(component: str, values: dict[str, str]) -> Resolution:
    if component in GITHUB_SOURCES:
        return resolve_github(component, values)
    if component == "svt-av1":
        return resolve_svt_av1(values)
    if component == "pytorch":
        return resolve_pytorch(values)
    if component == "node":
        return resolve_simple_image(
            component,
            "FRONTEND_NODE_IMAGE",
            values,
            re.compile(r"^(\d+(?:\.\d+)*)-bookworm-slim$"),
            "bookworm-slim",
        )
    if component == "pnpm":
        return resolve_pnpm(values)
    if component == "tool-python":
        return resolve_simple_image(
            component,
            "TOOL_PYTHON_IMAGE",
            values,
            re.compile(rf"^{STABLE_TAG_RE}-slim-bookworm$"),
            "slim-bookworm",
        )
    if component == "torchcodec":
        return resolve_torchcodec(values)
    raise VersionUpdateError(f"unknown version component: {component}")


def parse_values(arguments: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for argument in arguments:
        if "=" not in argument:
            raise VersionUpdateError(f"invalid version profile assignment: {argument!r}")
        key, value = argument.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise VersionUpdateError(f"invalid or duplicate version profile key: {key!r}")
        if any(character in value for character in "\r\n\0|"):
            raise VersionUpdateError(f"unsafe version profile value for {key}")
        values[key] = value
    return values


def print_resolution(resolution: Resolution) -> None:
    if resolution.updates:
        print(
            f"{resolution.component}: {resolution.current} -> {resolution.latest}",
            file=sys.stderr,
        )
        for note in resolution.notes:
            print(note, file=sys.stderr)
    else:
        print(
            f"{resolution.component}: {resolution.current} (latest eligible)",
            file=sys.stderr,
        )


def main() -> None:
    if sys.argv[1:] == ["list"]:
        print("\n".join(COMPONENTS))
        return
    if len(sys.argv) < 4 or sys.argv[1] != "resolve":
        fail("usage: update-versions.py list | resolve COMPONENT|all KEY=VALUE ...")
    selection = sys.argv[2]
    if selection != "all" and selection not in COMPONENTS:
        fail(f"unknown version component {selection!r}; choose from: {', '.join(COMPONENTS)}, all")
    try:
        values = parse_values(sys.argv[3:])
        selected = COMPONENTS if selection == "all" else (selection,)
        resolutions = tuple(resolve_component(component, values) for component in selected)
    except VersionUpdateError as error:
        fail(str(error))

    for resolution in resolutions:
        print_resolution(resolution)
    updates = [
        (resolution.component, key, value)
        for resolution in resolutions
        for key, value in resolution.updates
    ]
    for component, key, value in updates:
        print(f"{UPDATE_PREFIX}|{component}|{key}|{value}")
    print(f"{RESULT_PREFIX}|{selection}|{len(updates)}")


if __name__ == "__main__":
    main()
