from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from pysearch.fetcher import Fetcher

logger = logging.getLogger(__name__)

SUCCESS_TTL = 24 * 3600  # cache a retrieved (or 4xx "no robots.txt") result for a day
FAILURE_TTL = 300        # re-check an unreachable robots.txt after five minutes


class RobotsCache:
    """Fetches and caches robots.txt per origin, following RFC 9309 failure rules.

    - 200: rules are parsed and obeyed.
    - 4xx (no robots.txt): everything is allowed.
    - 5xx, 429, or a network failure (unreachable): everything is treated as
      disallowed until the short failure TTL expires and it is retried.
    """

    def __init__(
        self, fetcher: Fetcher, user_agent: str, clock: Callable[[], float] = time.monotonic
    ):
        self._fetcher = fetcher
        self._agent = user_agent
        self._clock = clock
        self._cache: dict[str, tuple[RobotFileParser, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _origin(url: str) -> str:
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}"

    def _fresh(self, origin: str) -> RobotFileParser | None:
        entry = self._cache.get(origin)
        if entry and entry[1] > self._clock():
            return entry[0]
        return None

    async def _parser_for(self, url: str) -> RobotFileParser:
        origin = self._origin(url)
        parser = self._fresh(origin)
        if parser is not None:
            return parser
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            parser = self._fresh(origin)
            if parser is None:
                parser, ttl = await self._load(origin)
                self._cache[origin] = (parser, self._clock() + ttl)
        return parser

    async def _load(self, origin: str) -> tuple[RobotFileParser, float]:
        parser = RobotFileParser()
        result = await self._fetcher.fetch(f"{origin}/robots.txt")
        if result is None or result.status >= 500 or result.status == 429:
            logger.warning("robots.txt unreachable for %s: assuming disallow-all", origin)
            parser.disallow_all = True
            ttl = FAILURE_TTL
        else:
            parser.parse(result.body.splitlines() if result.status == 200 else [])
            ttl = SUCCESS_TTL
        parser.modified()  # mark as checked, otherwise can_fetch() returns False
        return parser, ttl

    async def allowed(self, url: str) -> bool:
        return (await self._parser_for(url)).can_fetch(self._agent, url)

    async def crawl_delay(self, url: str) -> float | None:
        delay = (await self._parser_for(url)).crawl_delay(self._agent)
        return float(delay) if delay is not None else None
