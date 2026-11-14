"""Talk to the model hosts: the one place a token is sent, and each only to its own provider's host."""

from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .. import LatentCrateError
from ..names import SHA256_RE
from .manifest import ModelFile

USER_AGENT = "LatentCrate-model-set-tool/1"
HUB_URL = "https://huggingface.co"
CIVITAI_URL = "https://civitai.com"
# The same API, account, and version ids behind Civitai's mature-content
# domain; requests go to CIVITAI_URL, and the token may follow a redirect here.
CIVITAI_RED_URL = "https://civitai.red"
# What a redirect target may hold: printable ASCII without spaces. Anything
# else urllib or http.client would refuse with the whole URL, signed query
# included, in its message, or fail to encode.
URL_CHARACTERS_RE = re.compile(r"[\x21-\x7e]+")
REDIRECT_CODES = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 10

# HTTP and HTTPS only (no file:, ftp:, or data: handler) and no redirect
# handler: a redirect raises HTTPError, and open_url decides whether a
# token travels to the new location.
OPENER = urllib.request.OpenerDirector()
for handler in (
    urllib.request.ProxyHandler(),
    urllib.request.HTTPHandler(),
    urllib.request.HTTPSHandler(),
    urllib.request.UnknownHandler(),
    urllib.request.HTTPDefaultErrorHandler(),
    urllib.request.HTTPErrorProcessor(),
):
    OPENER.add_handler(handler)


def origin(url: str) -> tuple[str, str | None, int | None]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme, parts.hostname, parts.port


def shown(url: str) -> str:
    """url for a message: no user info, query, or fragment, where a signed
    redirect carries its credential."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    host += "" if parts.port is None else f":{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))


@dataclass(frozen=True)
class Tokens:
    """The credentials the CLI captured, by provider."""

    huggingface: str | None = field(default=None, repr=False)
    civitai: str | None = field(default=None, repr=False)

    def for_url(self, url: str) -> str | None:
        """The token of the provider whose origin url is, or None: compared
        per request, so a redirect to any other host (a provider's CDN) gets none."""
        return {"huggingface": self.huggingface, "civitai": self.civitai}.get(provider_of(url))


def provider_of(url: str) -> str | None:
    """The provider whose origin url is, or None."""
    homes = (("huggingface", HUB_URL), ("civitai", CIVITAI_URL), ("civitai", CIVITAI_RED_URL))
    return next((name for name, home in homes if origin(url) == origin(home)), None)


def open_url(url: str, headers: dict[str, str], tokens: Tokens, *, method: str = "GET"):
    """Request url and return the response; a token is sent to its provider's origin only.

    Redirects are followed here so each hop is judged anew: the
    Authorization header is dropped when the target is another origin (the
    Hugging Face or Civitai CDN) or another provider's than the requested
    URL's, and a redirect off the requested URL's scheme (https) is refused
    before anything is sent to it."""
    scheme = urllib.parse.urlsplit(url).scheme
    requested = provider_of(url)
    for _ in range(MAX_REDIRECTS + 1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers}, method=method)
        token = tokens.for_url(url) if provider_of(url) == requested else None
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            return OPENER.open(request, timeout=60)
        except urllib.error.HTTPError as error:
            error.close()
            if error.code not in REDIRECT_CODES or not error.headers.get("Location"):
                raise
            url = redirect_target(url, error.headers["Location"])
            if urllib.parse.urlsplit(url).scheme != scheme:
                raise LatentCrateError(f"refusing a redirect away from {scheme}: {shown(url)}") from None
    raise LatentCrateError(f"too many redirects for {shown(url)}")


def redirect_target(url: str, location: str) -> str:
    """The absolute URL a redirect names. One urllib would not connect to as
    parsed (whitespace, control or non-ASCII characters, user info, a
    backslash, a bad port) is refused here, so neither the token nor a
    signed query reaches an error message."""
    try:
        target = urllib.parse.urljoin(url, location)
        origin(target)  # raises ValueError for a malformed host or port
    except ValueError:
        raise LatentCrateError(f"refusing a malformed redirect from {shown(url)}") from None
    if not URL_CHARACTERS_RE.fullmatch(target) or "@" in urllib.parse.urlsplit(target).netloc or "\\" in target:
        raise LatentCrateError(f"refusing a malformed redirect from {shown(url)}")
    return target


def civitai_record(item: dict) -> tuple[int | None, str | None]:
    """The byte size and lowercase SHA-256 Civitai records for a file, where
    it records them; sizeKB is the byte count over 1024."""
    size_kb = item.get("sizeKB")
    size = size_kb * 1024 if isinstance(size_kb, (int, float)) and not isinstance(size_kb, bool) else None
    digest = (item.get("hashes") or {}).get("SHA256")
    digest = digest.lower() if isinstance(digest, str) and SHA256_RE.fullmatch(digest.lower()) else None
    return (int(size) if size is not None and size > 0 and size == int(size) else None), digest


def civitai_files(payload: dict) -> list[dict]:
    return [item for item in payload.get("files", []) if isinstance(item, dict)]


def version_file(version: int, payload: dict, file_id: int | None = None) -> dict:
    """The file of a Civitai version record with id file_id, or without one
    the file it marks primary, or its only file. Without either, the
    refusal lists every file for the author to choose one as civitai_file;
    names come from the uploader, so they are shown quoted."""
    files = civitai_files(payload)
    if file_id is not None:
        chosen = [item for item in files if item.get("id") == file_id]
        if len(chosen) != 1:
            raise LatentCrateError(f"Civitai model version {version} has no file {file_id}")
        return chosen[0]
    primary = files if len(files) == 1 else [item for item in files if item.get("primary") is True]
    if len(primary) != 1:
        listed = ", ".join(
            f"{item.get('id')!r} {item.get('name')!r} ({civitai_record(item)[0] or 'unknown'} bytes)" for item in files
        )
        raise LatentCrateError(
            f"Civitai model version {version} has no single primary file; name one as civitai_file: {listed or 'none'}"
        )
    return primary[0]


def file_url(model_file: ModelFile, tokens: Tokens) -> str:
    """The download URL, built here and never read from the manifest. A
    Civitai version is asked for its chosen file by id: without one,
    Civitai picks a file by the signed-in account's preferences."""
    if model_file.civitai_version is not None:
        version = model_file.civitai_version
        chosen = version_file(version, hub_json(version_url(version), tokens), model_file.civitai_file)
        file_id = chosen.get("id")
        if not isinstance(file_id, int) or isinstance(file_id, bool) or file_id <= 0:
            raise LatentCrateError(f"Civitai names no file id for the primary file of model version {version}")
        return f"{CIVITAI_URL}/api/download/models/{version}?fileId={file_id}"
    return f"{HUB_URL}/{model_file.repository}/resolve/{model_file.revision}/{urllib.parse.quote(model_file.source)}"


def revision_url(repository: str, revision: str) -> str:
    """The metadata of a repository at a revision, with the size and LFS checksum of every file."""
    return f"{HUB_URL}/api/models/{urllib.parse.quote(repository, safe='/')}/revision/{revision}?blobs=true"


def version_url(version: int) -> str:
    """Civitai's record of a model version, with the size and SHA-256 of every file."""
    return f"{CIVITAI_URL}/api/v1/model-versions/{version}"


def check_access(model_file: ModelFile, tokens: Tokens) -> None:
    civitai = model_file.civitai_version is not None
    provider = "Civitai" if civitai else "Hugging Face"
    where = model_file.origin
    try:
        open_url(file_url(model_file, tokens), {"Range": "bytes=0-0"}, tokens).close()
    except urllib.error.HTTPError as error:
        if error.code in (401, 403) and civitai and not tokens.civitai:
            raise LatentCrateError(
                f"cannot access {where} (HTTP {error.code}). "
                "Civitai serves it to signed-in accounts only: set CIVITAI_TOKEN in .env."
            ) from error
        if error.code in (401, 403) and civitai:
            raise LatentCrateError(
                f"cannot access {where} (HTTP {error.code}) with CIVITAI_TOKEN. "
                f"Check on its model page, {CIVITAI_URL}/model-versions/{model_file.civitai_version}, "
                "that your account may download it (early access, purchase, region)."
            ) from error
        if error.code in (401, 403):
            raise LatentCrateError(
                f"cannot access {where} (HTTP {error.code}). "
                "For gated repositories, accept the license and set HF_TOKEN in .env."
            ) from error
        if error.code == 404:
            missing = "the version or its file was removed" if civitai else "the file or the revision is missing"
            raise LatentCrateError(f"{where} does not exist: {missing}") from error
        raise LatentCrateError(f"{provider} returned HTTP {error.code} for {where}") from error
    except (OSError, http.client.HTTPException) as error:
        raise LatentCrateError(f"could not reach {provider} for {where}: {error}") from error


def hub_json(url: str, tokens: Tokens) -> dict:
    provider = "Hugging Face" if origin(url) == origin(HUB_URL) else "Civitai"
    try:
        with open_url(url, {"Accept": "application/json"}, tokens) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise LatentCrateError(f"{provider} returned HTTP {error.code} for {shown(url)}") from error
    except (OSError, http.client.HTTPException) as error:
        raise LatentCrateError(f"could not query {provider}: {error}") from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise LatentCrateError(f"{provider} answered {shown(url)} with something other than JSON") from error
    if not isinstance(payload, dict):
        raise LatentCrateError(f"unexpected {provider} response for {shown(url)}")
    return payload
