"""Talk to the Hugging Face Hub: the one place the token is sent, and only to the Hub host."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

from .. import LatentCrateError
from .manifest import ModelFile

USER_AGENT = "LatentCrate-model-set-tool/1"
HUB_URL = "https://huggingface.co"
REDIRECT_CODES = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 10

# HTTP and HTTPS only (no file:, ftp:, or data: handler) and no redirect
# handler: a redirect raises HTTPError, and open_url decides whether the
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


def open_url(url: str, headers: dict[str, str], token: str | None, *, method: str = "GET"):
    """Request url and return the response; the token is sent to the Hub host only.

    Redirects are followed here so the Authorization header is dropped when
    the target is another host (the Hugging Face CDN), and a redirect off the
    Hub's URL scheme (https) is refused before anything is sent to it."""
    hub = urllib.parse.urlsplit(HUB_URL)
    for _ in range(MAX_REDIRECTS + 1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers}, method=method)
        if token and urllib.parse.urlsplit(url).hostname == hub.hostname:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            return OPENER.open(request, timeout=60)
        except urllib.error.HTTPError as error:
            error.close()
            if error.code not in REDIRECT_CODES or not error.headers.get("Location"):
                raise
            url = urllib.parse.urljoin(url, error.headers["Location"])
            if urllib.parse.urlsplit(url).scheme != hub.scheme:
                raise LatentCrateError(f"refusing a redirect away from {hub.scheme}: {url}") from None
    raise LatentCrateError(f"too many redirects for {url}")


def file_url(model_file: ModelFile) -> str:
    return f"{HUB_URL}/{model_file.repository}/resolve/{model_file.revision}/{urllib.parse.quote(model_file.source)}"


def revision_url(repository: str, revision: str) -> str:
    """The metadata of a repository at a revision, with the size and LFS checksum of every file."""
    return f"{HUB_URL}/api/models/{urllib.parse.quote(repository, safe='/')}/revision/{revision}?blobs=true"


def check_access(model_file: ModelFile, token: str | None) -> None:
    where = f"{model_file.repository}/{model_file.source} at {model_file.revision}"
    try:
        open_url(file_url(model_file), {"Range": "bytes=0-0"}, token).close()
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise LatentCrateError(
                f"cannot access {where} (HTTP {error.code}). "
                "For gated repositories, accept the license and set HF_TOKEN in .env."
            ) from error
        if error.code == 404:
            raise LatentCrateError(f"{where} does not exist: the file or the revision is missing") from error
        raise LatentCrateError(f"Hugging Face returned HTTP {error.code} for {where}") from error
    except (OSError, http.client.HTTPException) as error:
        raise LatentCrateError(f"could not reach Hugging Face for {where}: {error}") from error


def hub_json(url: str, token: str | None) -> dict:
    try:
        with open_url(url, {"Accept": "application/json"}, token) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise LatentCrateError(f"Hugging Face returned HTTP {error.code} for {url}") from error
    except (OSError, http.client.HTTPException, ValueError) as error:
        raise LatentCrateError(f"could not query Hugging Face: {error}") from error
    if not isinstance(payload, dict):
        raise LatentCrateError(f"unexpected Hugging Face response for {url}")
    return payload
