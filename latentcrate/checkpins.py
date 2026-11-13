"""Report the latest eligible upstream versions of the pins in pins.env next to the current ones."""

from __future__ import annotations

import functools
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import LatentCrateError

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
# Docker Hub answers an anonymous client's tag listing for ten pages of 100
# and refuses the eleventh (HTTP 403), so a lookup must find its answer in
# the first thousand tags, newest first.
DOCKER_HUB_PAGE_LIMIT = 10
USER_AGENT = "LatentCrate-pins-check"
GITHUB_ACCEPT = "application/vnd.github+json"
STABLE_TAG_RE = r"(\d+(?:\.\d+)+)"
PACKAGE_VERSION_RE = re.compile(rf"^{STABLE_TAG_RE}$")
# A PyTorch image tag: the torch release, the CUDA family (cuda13.0-cudnn9),
# and the flavour the Dockerfile pulls.
PYTORCH_TAG_RE = re.compile(rf"^{STABLE_TAG_RE}-(?P<family>cuda\d+\.\d+-cudnn\d+)-(?P<flavour>devel|runtime)$")
# The TorchCodec wheel with the `+cuNNN` suffix of its index; `env.cuda_family`
# ties the suffix to the PyTorch tag, so the lookup keeps it.
TORCHCODEC_VERSION_RE = re.compile(rf"^{STABLE_TAG_RE}\+(?P<family>cu\d+)$")
# What pins.env holds beside a tag: the 64 hex characters of the manifest the
# tag points at, which is what the build pulls.
DIGEST_RE = re.compile(r"^sha256:(?P<digest>[0-9a-f]{64})$")


class LookupsFailed(LatentCrateError):
    """The report of a `pins check` in which at least one source could not
    be read; `report` holds every row, `failed` the component names."""

    def __init__(self, report: str, failed: list[str]) -> None:
        super().__init__(f"pins check could not read the source of: {', '.join(failed)}")
        self.report = report
        self.failed = failed


@dataclass(frozen=True)
class Resolution:
    key: str
    current: str
    latest: str
    notes: tuple[str, ...] = ()
    # The manifest digest of `latest`, for the <name>_DIGEST pin beside the
    # tag. Empty for a version or ref pin that has none, and for a current
    # pin, which is not pasted anywhere.
    digest: str = ""


def component_name(key: str) -> str:
    """The name `pins check` takes for a pin: its key without the `_TAG`,
    `_VERSION`, or `_REF` suffix, in lower case with hyphens
    (NV_CODEC_HEADERS_REF: nv-codec-headers)."""
    return key.removesuffix("_TAG").removesuffix("_VERSION").removesuffix("_REF").lower().replace("_", "-")


class HttpsRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect to an HTTPS address only."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() != "https":
            raise LatentCrateError(f"pin source redirected away from HTTPS: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(HttpsRedirects)


def request_bytes(url: str, *, accept: str = "application/json") -> bytes:
    if urllib.parse.urlsplit(url).scheme.lower() != "https":
        raise LatentCrateError(f"pin source URL must use HTTPS: {url}")
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": USER_AGENT})
    try:
        with OPENER.open(request, timeout=45) as response:
            content = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        error.close()
        detail = f"HTTP {error.code}"
        response_headers = error.headers or {}
        remaining = response_headers.get("X-RateLimit-Remaining")
        retry_after = response_headers.get("Retry-After")
        if remaining is not None:
            detail += f", rate limit remaining {remaining}"
        if retry_after is not None:
            detail += f", retry after {retry_after} seconds"
        raise LatentCrateError(f"could not read pin source {url}: {detail}") from error
    except (OSError, urllib.error.URLError) as error:
        raise LatentCrateError(f"could not read pin source {url}: {error}") from error
    if len(content) > MAX_RESPONSE_BYTES:
        raise LatentCrateError(f"pin source response is too large: {url}")
    return content


def request_json(url: str, *, accept: str = "application/json") -> Any:
    content = request_bytes(url, accept=accept)
    try:
        return json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LatentCrateError(f"pin source returned invalid JSON: {url}") from error


def version_key(value: str, pattern: re.Pattern[str]) -> tuple[int, ...] | None:
    match = pattern.fullmatch(value)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def latest_tag(tags: list[str], pattern: re.Pattern[str], source: str) -> str:
    candidates = [(parsed, tag) for tag in tags if (parsed := version_key(tag, pattern)) is not None]
    if not candidates:
        raise LatentCrateError(f"{source} returned no stable version tags")
    return max(candidates)[1]


def current_is_latest(current: tuple[int, ...], latest: tuple[int, ...], source: str) -> bool:
    if latest < current:
        raise LatentCrateError(f"{source} latest eligible version is older than the current pin")
    return latest == current


def digest_key(key: str) -> str:
    """Every tag pin has a digest pin beside it (PYTORCH_DEVEL_TAG,
    PYTORCH_DEVEL_DIGEST), because a tag can be moved."""
    return key.removesuffix("_TAG") + "_DIGEST"


def manifest_digest(value: str, source: str) -> str:
    """The hex digest of a `sha256:...` reference, as pins.env spells it."""
    match = DIGEST_RE.fullmatch(value)
    if match is None:
        raise LatentCrateError(f"{source} returned no sha256 manifest digest")
    return match.group("digest")


def required_value(values: dict[str, str], key: str) -> str:
    value = values.get(key, "")
    if not value:
        raise LatentCrateError(f"pins.env has no value for {key}")
    return value


def current_key(values: dict[str, str], key: str, pattern: re.Pattern[str]) -> tuple[str, tuple[int, ...]]:
    """The pinned value and its version; a value the resolver cannot rank
    (a 40-hex commit under a `_REF` key, a prerelease) is refused."""
    current = required_value(values, key)
    parsed = version_key(current, pattern)
    if parsed is None:
        raise LatentCrateError(f"unsupported current {key}: {current}")
    return current, parsed


# --- Git tags: GitHub releases, GitHub tags, GitLab tags ---------------------


def github_latest_release(repository: str) -> list[str]:
    """The tag of the newest release GitHub marks as latest, which excludes
    prereleases (ComfyUI_frontend publishes them)."""
    payload = request_json(f"https://api.github.com/repos/{repository}/releases/latest", accept=GITHUB_ACCEPT)
    tag = payload.get("tag_name") if isinstance(payload, dict) else None
    if not isinstance(tag, str) or not tag:
        raise LatentCrateError(f"GitHub {repository} has no latest release")
    return [tag]


def github_tags(repository: str) -> list[str]:
    """The newest hundred tags of a repository that publishes no releases."""
    payload = request_json(f"https://api.github.com/repos/{repository}/tags?per_page=100", accept=GITHUB_ACCEPT)
    if not isinstance(payload, list):
        raise LatentCrateError(f"GitHub {repository} returned an invalid tag list")
    return [str(item["name"]) for item in payload if isinstance(item, dict) and item.get("name")]


def gitlab_tags(project: str) -> list[str]:
    encoded = urllib.parse.quote(project, safe="")
    payload = request_json(f"https://gitlab.com/api/v4/projects/{encoded}/repository/tags?per_page=100")
    if not isinstance(payload, list):
        raise LatentCrateError(f"GitLab {project} returned an invalid tag list")
    return [str(item["name"]) for item in payload if isinstance(item, dict) and item.get("name")]


def resolve_ref(
    key: str, source: str, pattern: re.Pattern[str], listing: Callable[[], list[str]], link: str, values: dict[str, str]
) -> Resolution:
    """Resolve a `_REF` pin against the tags `listing` returns."""
    current, parsed = current_key(values, key, pattern)
    latest = latest_tag(listing(), pattern, source)
    if current_is_latest(parsed, version_key(latest, pattern), source):
        return Resolution(key, current, current)
    return Resolution(key, current, latest, (f"  Release history: {link}",))


def github_ref(key: str, repository: str, pattern: re.Pattern[str], *, releases: bool) -> Callable[..., Resolution]:
    listing = functools.partial(github_latest_release if releases else github_tags, repository)
    link = f"https://github.com/{repository}/" + ("releases" if releases else "tags")
    return functools.partial(resolve_ref, key, f"GitHub {repository}", pattern, listing, link)


# --- Package registries: npm and PyPI ----------------------------------------


def resolve_pnpm(values: dict[str, str]) -> Resolution:
    key = "FRONTEND_PNPM_VERSION"
    current, parsed = current_key(values, key, PACKAGE_VERSION_RE)
    payload = request_json("https://registry.npmjs.org/pnpm/latest")
    latest = payload.get("version", "") if isinstance(payload, dict) else ""
    latest_key = version_key(latest, PACKAGE_VERSION_RE)
    if latest_key is None:
        raise LatentCrateError("npm returned no stable pnpm latest version")
    if current_is_latest(parsed, latest_key, "npm pnpm"):
        return Resolution(key, current, current)
    return Resolution(
        key, current, latest, ("  Package history: https://www.npmjs.com/package/pnpm?activeTab=versions",)
    )


def resolve_torchcodec(values: dict[str, str]) -> Resolution:
    key = "TORCHCODEC_VERSION"
    current, parsed = current_key(values, key, TORCHCODEC_VERSION_RE)
    payload = request_json("https://pypi.org/pypi/torchcodec/json")
    info = payload.get("info") if isinstance(payload, dict) else None
    latest = str(info.get("version", "")) if isinstance(info, dict) else ""
    latest_key = version_key(latest, PACKAGE_VERSION_RE)
    if latest_key is None:
        raise LatentCrateError("PyPI returned no stable torchcodec latest version")
    if current_is_latest(parsed, latest_key, "PyPI torchcodec"):
        return Resolution(key, current, current)
    family = TORCHCODEC_VERSION_RE.fullmatch(current).group("family")
    # PyPI lists the CPU wheel only; the CUDA wheel of a release may lag.
    notes = (
        "  Release history: https://github.com/meta-pytorch/torchcodec/releases",
        f"  Verify on https://download.pytorch.org/whl/{family}/torchcodec/",
    )
    return Resolution(key, current, f"{latest}+{family}", notes)


# --- Docker Hub --------------------------------------------------------------


def docker_hub_tags(
    namespace: str,
    repository: str,
    search: str,
    rank: Callable[[str], tuple[int, ...] | None] | None = None,
) -> dict[str, str]:
    """The matching tags of the image, newest push first, each with the
    `sha256:...` reference of the manifest it points at; the listing carries
    both. Docker Hub lists at most ten pages to an anonymous client, and
    an image such as node has over a thousand tags, so the listing is read
    newest first and stops after the first page that adds no tag `rank`
    places at or above the best one seen (a release's tags are pushed
    together, so they are contiguous in the listing and may straddle a page
    boundary)."""
    query = urllib.parse.urlencode({"page_size": 100, "name": search, "ordering": "last_updated"})
    url: str | None = f"https://hub.docker.com/v2/namespaces/{namespace}/repositories/{repository}/tags?{query}"
    tags: dict[str, str] = {}
    best: tuple[int, ...] | None = None
    pages = 0
    while url:
        if pages >= DOCKER_HUB_PAGE_LIMIT:
            raise LatentCrateError(
                f"Docker Hub tags for {namespace}/{repository} exceeded the {DOCKER_HUB_PAGE_LIMIT}-page listing limit"
            )
        payload = request_json(url)
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise LatentCrateError(f"Docker Hub returned invalid tags for {namespace}/{repository}")
        page = {
            str(item.get("name", "")): str(item.get("digest", ""))
            for item in payload["results"]
            if isinstance(item, dict)
        }
        tags.update(page)
        pages += 1
        if rank is not None:
            ranked = [key for tag in page if (key := rank(tag)) is not None]
            page_best = max(ranked) if ranked else None
            if best is not None and (page_best is None or page_best < best):
                break
            if page_best is not None:
                best = page_best if best is None else max(best, page_best)
        next_url = payload.get("next")
        if next_url:
            next_url = str(next_url)
            parsed_next = urllib.parse.urlsplit(next_url)
            if parsed_next.scheme != "https" or parsed_next.hostname != "hub.docker.com":
                raise LatentCrateError("Docker Hub returned an unsafe pagination link")
            url = next_url
        else:
            url = None
    return tags


def resolve_tag(
    key: str,
    namespace: str,
    repository: str,
    pattern: re.Pattern[str],
    values: dict[str, str],
    *,
    search: str,
    even_major_only: bool = False,
) -> Resolution:
    """Resolve a tag-valued pin of the Docker Hub image namespace/repository
    among the tags `search` narrows the listing to."""
    current, parsed = current_key(values, key, pattern)
    published = docker_hub_tags(namespace, repository, search, lambda tag: version_key(tag, pattern))
    candidates = [
        tag
        for tag in published
        if (candidate := version_key(tag, pattern)) is not None
        and len(candidate) == len(parsed)
        # Node.js ships long-term-support releases on even majors only.
        and (not even_major_only or candidate[0] % 2 == 0)
    ]
    source = f"Docker Hub {namespace}/{repository}"
    latest = latest_tag(candidates, pattern, source)
    if current_is_latest(parsed, version_key(latest, pattern), source):
        return Resolution(key, current, current)
    image_history = (
        f"https://hub.docker.com/_/{repository}"
        if namespace == "library"
        else f"https://hub.docker.com/r/{namespace}/{repository}/tags"
    )
    return Resolution(
        key, current, latest, (f"  Image tags: {image_history}",), manifest_digest(published[latest], source)
    )


@functools.lru_cache
def pytorch_tags(family: str) -> dict[str, str]:
    """The pytorch/pytorch tags of one CUDA family, listed once per process
    for the two pins that share it."""
    return docker_hub_tags("pytorch", "pytorch", family, lambda tag: version_key(tag, PYTORCH_TAG_RE))


def resolve_pytorch(key: str, values: dict[str, str]) -> Resolution:
    """Resolve one of the two PyTorch image pins within the pinned CUDA
    family (a new family is a manual choice, since TorchCodec and NPP follow
    it) to the newest torch release that has both the devel and the runtime
    tag, because `env.cuda_family` demands the two pins move together."""
    current, parsed = current_key(values, key, PYTORCH_TAG_RE)
    match = PYTORCH_TAG_RE.fullmatch(current)
    family, flavour = match.group("family"), match.group("flavour")
    published = pytorch_tags(family)
    releases: dict[tuple[int, ...], dict[str, str]] = {}
    for tag in published:
        listed = PYTORCH_TAG_RE.fullmatch(tag)
        if listed is not None and listed.group("family") == family:
            releases.setdefault(version_key(tag, PYTORCH_TAG_RE), {})[listed.group("flavour")] = tag
    complete = [(release, pair) for release, pair in releases.items() if len(pair) == 2]
    source = "Docker Hub pytorch/pytorch"
    if not complete:
        raise LatentCrateError(f"{source} lists no devel and runtime tag pair of the {family} family")
    latest_key, pair = max(complete)
    if current_is_latest(parsed, latest_key, source):
        return Resolution(key, current, current)
    latest = pair[flavour]
    notes = ("  Image tags: https://hub.docker.com/r/pytorch/pytorch/tags",)
    return Resolution(key, current, latest, notes, manifest_digest(published[latest], source))


# One resolver per pin key the check can look up; the digest beside a tag is
# resolved with the tag. A pin of pins.env missing here is reported as such:
# CUDA_NPP_VERSION follows the devel image's bundled NPP, which no index
# lists ahead of the image.
RESOLVERS: dict[str, Callable[[dict[str, str]], Resolution]] = {
    "PYTORCH_DEVEL_TAG": functools.partial(resolve_pytorch, "PYTORCH_DEVEL_TAG"),
    "PYTORCH_RUNTIME_TAG": functools.partial(resolve_pytorch, "PYTORCH_RUNTIME_TAG"),
    "TORCHCODEC_VERSION": resolve_torchcodec,
    "COMFYUI_REF": github_ref("COMFYUI_REF", "Comfy-Org/ComfyUI", re.compile(rf"^v{STABLE_TAG_RE}$"), releases=True),
    "COMFYUI_FRONTEND_REF": github_ref(
        "COMFYUI_FRONTEND_REF", "Comfy-Org/ComfyUI_frontend", re.compile(rf"^v{STABLE_TAG_RE}$"), releases=True
    ),
    "FFMPEG_REF": github_ref("FFMPEG_REF", "FFmpeg/FFmpeg", re.compile(rf"^n{STABLE_TAG_RE}$"), releases=False),
    "NV_CODEC_HEADERS_REF": github_ref(
        "NV_CODEC_HEADERS_REF", "FFmpeg/nv-codec-headers", re.compile(rf"^n{STABLE_TAG_RE}$"), releases=False
    ),
    "SVT_AV1_REF": functools.partial(
        resolve_ref,
        "SVT_AV1_REF",
        "GitLab AOMediaCodec/SVT-AV1",
        re.compile(rf"^v{STABLE_TAG_RE}$"),
        functools.partial(gitlab_tags, "AOMediaCodec/SVT-AV1"),
        "https://gitlab.com/AOMediaCodec/SVT-AV1/-/tags",
    ),
    "SAGEATTENTION_REF": github_ref(
        "SAGEATTENTION_REF", "thu-ml/SageAttention", re.compile(rf"^v{STABLE_TAG_RE}$"), releases=False
    ),
    "FRONTEND_NODE_TAG": functools.partial(
        resolve_tag,
        "FRONTEND_NODE_TAG",
        "library",
        "node",
        re.compile(r"^(\d+(?:\.\d+)*)-bookworm-slim$"),
        search="bookworm-slim",
        even_major_only=True,
    ),
    "FRONTEND_PNPM_VERSION": resolve_pnpm,
}


def check(selection: str, values: dict[str, str]) -> str:
    """Resolve the selected pin (or every pin of pins.env) and return the report text.

    Every pin is a row, named by `component_name`; a pin without a resolver
    says so, so what the check covers is in its output. A source that
    cannot be read makes its pin's row say so and the whole report is
    raised as `LookupsFailed`, so the other pins are still reported and
    the exit status says a lookup failed."""
    components = {component_name(key): key for key in values if not key.endswith("_DIGEST")}
    if selection != "all" and selection not in components:
        raise LatentCrateError(f"unknown pin component {selection!r}; choose from: {', '.join(components)}, all")
    selected = components if selection == "all" else {selection: components[selection]}
    rows: dict[str, Resolution | LatentCrateError | None] = {}
    for name, key in selected.items():
        if key not in RESOLVERS:
            rows[name] = None
            continue
        try:
            rows[name] = RESOLVERS[key](values)
        except LatentCrateError as error:
            rows[name] = error

    lines: list[str] = []
    for name, resolution in rows.items():
        if resolution is None:
            lines.append(f"{name}: {values[selected[name]]} (no resolver; look it up by hand)")
        elif isinstance(resolution, LatentCrateError):
            lines.append(f"{name}: {values[selected[name]]} (lookup failed: {resolution})")
        elif resolution.latest == resolution.current:
            lines.append(f"{name}: {resolution.current} (latest eligible)")
        else:
            lines.append(f"{name}: {resolution.current} -> {resolution.latest}")
            lines.extend(resolution.notes)
    resolved = [resolution for resolution in rows.values() if isinstance(resolution, Resolution)]
    failed = [name for name, resolution in rows.items() if isinstance(resolution, LatentCrateError)]
    newer = [resolution for resolution in resolved if resolution.latest != resolution.current]
    if newer:
        lines += ["", "Paste into pins.env:"]
        for resolution in newer:
            lines.append(f"{resolution.key}={resolution.latest}")
            if resolution.digest:
                lines.append(f"{digest_key(resolution.key)}={resolution.digest}")
    elif len(resolved) == len(rows):
        lines.append("pins.env has the latest eligible pins.")
    elif resolved:
        lines.append("The resolved pins are the latest eligible.")
    report = "\n".join(lines) + "\n"
    if failed:
        raise LookupsFailed(report, failed)
    return report
