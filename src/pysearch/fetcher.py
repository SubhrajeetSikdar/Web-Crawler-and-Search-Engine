from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from pysearch.security import is_public_ip, url_is_public
from pysearch.urls import normalize_url

logger = logging.getLogger(__name__)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# Called with each redirect target BEFORE it is requested; return False to veto it.
RedirectValidator = Callable[[str], Awaitable[bool]]


class RedirectBlocked(Exception):
    """A redirect target was vetoed by the caller's policy and was never requested."""

    def __init__(self, url: str):
        super().__init__(url)
        self.url = url


@dataclass
class FetchResult:
    url: str  # final URL after redirects
    status: int
    content_type: str
    body: str
    truncated: bool = False  # the response exceeded max_bytes and was cut short


class Fetcher(Protocol):
    async def fetch(
        self, url: str, validate: RedirectValidator | None = None
    ) -> FetchResult | None: ...

    async def close(self) -> None: ...


def _public_only_resolver():
    """DNS resolver that refuses non-public answers.

    Because the connection uses the addresses returned here, a hostname cannot pass a
    pre-check and then rebind to an internal IP before the socket opens (DNS rebinding).
    """
    import aiohttp
    from aiohttp.abc import AbstractResolver

    class PublicOnlyResolver(AbstractResolver):
        def __init__(self) -> None:
            self._inner = aiohttp.ThreadedResolver()

        async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
            infos = await self._inner.resolve(host, port, family)
            safe = [info for info in infos if is_public_ip(info["host"])]
            if not safe:
                raise OSError(f"{host} resolves only to non-public addresses")
            return safe

        async def close(self) -> None:
            await self._inner.close()

    return PublicOnlyResolver()


class HttpFetcher:
    """aiohttp fetcher: timeouts, size cap, validated redirects, SSRF protection."""

    def __init__(
        self,
        user_agent: str,
        timeout: float = 15.0,
        max_bytes: int = 2_000_000,
        max_redirects: int = 5,
        allow_private_hosts: bool = False,
    ):
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.allow_private_hosts = allow_private_hosts
        self._session = None

    def _get_session(self):
        if self._session is None:
            import aiohttp  # imported lazily so the core stays dependency-light

            connector = None
            if not self.allow_private_hosts:
                connector = aiohttp.TCPConnector(resolver=_public_only_resolver())
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": self.user_agent, "Accept": "text/html,text/plain;q=0.9"},
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                connector=connector,
                trust_env=False,  # never route through env proxies that bypass the resolver
            )
        return self._session

    async def fetch(
        self, url: str, validate: RedirectValidator | None = None
    ) -> FetchResult | None:
        """GET a URL, following redirects by hand.

        Every redirect target is passed to `validate` before it is requested, so crawl
        policy (same-site, robots.txt, SSRF) is enforced before any request is sent.
        Raises RedirectBlocked if the validator vetoes a hop; returns None on network
        errors, timeouts, redirect loops or non-public addresses.
        """
        import aiohttp

        session = self._get_session()
        current = url
        for _ in range(self.max_redirects + 1):
            if not self.allow_private_hosts and not url_is_public(current):
                logger.warning("refusing non-public address: %s", current)
                return None
            try:
                async with session.get(current, allow_redirects=False) as resp:
                    if resp.status in _REDIRECT_STATUSES:
                        location = resp.headers.get("Location")
                        target = normalize_url(location, current) if location else None
                        if target is None:
                            return None
                        if validate is not None and not await validate(target):
                            raise RedirectBlocked(target)
                        current = target
                        continue
                    return await self._read(resp, current)
            except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError) as exc:
                logger.debug("fetch failed for %s: %s", current, exc)
                return None
        logger.debug("too many redirects starting at %s", url)
        return None

    async def _read(self, resp, url: str) -> FetchResult:
        declared = resp.headers.get("Content-Length", "")
        truncated = declared.isdigit() and int(declared) > self.max_bytes
        raw = b""
        if not truncated:
            # StreamReader.read(n) may return fewer than n bytes even when more is coming,
            # so read chunk by chunk until the cap is exceeded or the body ends.
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.content.iter_chunked(65536):
                chunks.append(chunk)
                total += len(chunk)
                if total > self.max_bytes:
                    truncated = True
                    break
            raw = b"".join(chunks)[: self.max_bytes]
        try:
            body = raw.decode(resp.charset or "utf-8", errors="replace")
        except LookupError:
            body = raw.decode("utf-8", errors="replace")
        return FetchResult(
            url=url,
            status=resp.status,
            content_type=resp.headers.get("Content-Type", ""),
            body=body,
            truncated=truncated,
        )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
