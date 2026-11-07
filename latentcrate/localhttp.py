"""One GET against the local service that ignores host proxy settings: the
health poll of `up` must reach the published port directly."""

from __future__ import annotations

from urllib.request import ProxyHandler, build_opener

# Host proxy settings apply to downloads, never to the service's loopback URL.
OPENER = build_opener(ProxyHandler({}))


def http_get(url: str, timeout: float = 5) -> str | None:
    """The body of a GET, or None when the request fails."""
    try:
        with OPENER.open(url, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return None
