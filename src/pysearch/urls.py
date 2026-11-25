from __future__ import annotations

from urllib.parse import urljoin, urlsplit, urlunsplit

SKIP_EXTENSIONS = frozenset(
    {
        "jpg", "jpeg", "png", "gif", "webp", "svg", "ico", "bmp", "pdf", "zip", "gz", "tar",
        "rar", "7z", "mp3", "mp4", "avi", "mov", "wav", "css", "js", "json", "xml", "woff",
        "woff2", "ttf", "eot", "exe", "dmg", "iso", "apk",
    }
)
_DEFAULT_PORTS = {("http", 80), ("https", 443)}


def normalize_url(url: str, base: str | None = None) -> str | None:
    """Return a canonical absolute http(s) URL, or None if it should not be crawled.

    Lowercases scheme and host, drops fragments, default ports and credentials,
    and rejects non-HTTP schemes and obvious binary or asset file types.
    """
    try:
        if base:
            url = urljoin(base, url.strip())
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if scheme not in ("http", "https") or not host:
        return None
    host = host.lower()
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = host if port is None or (scheme, port) in _DEFAULT_PORTS else f"{host}:{port}"
    path = parts.path or "/"
    last = path.rsplit("/", 1)[-1]
    if "." in last and last.rsplit(".", 1)[-1].lower() in SKIP_EXTENSIONS:
        return None
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()
