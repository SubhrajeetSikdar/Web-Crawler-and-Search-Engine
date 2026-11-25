from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from pysearch.config import CrawlConfig
from pysearch.fetcher import FetchResult, Fetcher, HttpFetcher, RedirectBlocked
from pysearch.index import Index
from pysearch.parser import parse_html
from pysearch.robots import RobotsCache
from pysearch.security import url_is_public
from pysearch.urls import host_of, normalize_url

logger = logging.getLogger(__name__)


def _site(host: str) -> str:
    """Host key for same-site comparison: example.com and www.example.com match."""
    return host[4:] if host.startswith("www.") else host


@dataclass
class CrawlStats:
    scheduled: int = 0
    fetched: int = 0
    indexed: int = 0
    skipped: int = 0         # duplicate content, noindex, no text, or vetoed redirect
    removed: int = 0         # previously indexed pages dropped (noindex, 404, 410)
    too_large: int = 0       # response exceeded max_bytes; not indexed
    robots_blocked: int = 0
    blocked: int = 0         # refused by the SSRF guard (non-public address)
    retries: int = 0
    errors: int = 0


class Crawler:
    """Polite, concurrent breadth-first crawler that feeds an Index.

    A Crawler can be reused: every call to crawl() starts from a clean slate (fresh
    stats, frontier and robots cache) and returns its own CrawlStats. Running two
    crawls at the same time on one instance raises RuntimeError.
    """

    def __init__(self, config: CrawlConfig, index: Index, fetcher: Fetcher | None = None):
        self.config = config
        self.index = index
        self.fetcher: Fetcher = fetcher or HttpFetcher(
            config.user_agent,
            config.timeout,
            config.max_bytes,
            config.max_redirects,
            config.allow_private_hosts,
        )
        self._running = False
        self._reset()

    def _reset(self) -> None:
        # asyncio primitives bind to the running loop, so they are created per run
        self.stats = CrawlStats()
        self.robots = RobotsCache(self.fetcher, self.config.user_agent)
        self._seen: set[str] = set()
        self._allowed_sites: set[str] = set()
        self._queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._next_allowed: dict[str, float] = {}

    async def crawl(self, seeds: list[str]) -> CrawlStats:
        if self._running:
            raise RuntimeError("this Crawler is already running; create a second instance")
        self._running = True
        try:
            self._reset()
            stats = self.stats
            for seed in seeds:
                url = normalize_url(seed)
                if url and self._public_ok(url):
                    self._allowed_sites.add(_site(host_of(url)))
                    self._enqueue(url, 0)
                else:
                    logger.warning("ignoring seed %s", seed)
            workers = [
                asyncio.create_task(self._worker()) for _ in range(self.config.concurrency)
            ]
            try:
                await self._queue.join()
            finally:
                for w in workers:
                    w.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                await self.fetcher.close()
            return stats
        finally:
            self._running = False

    # ---- scheduling -------------------------------------------------------------

    def _public_ok(self, url: str) -> bool:
        """SSRF guard: only public hosts unless explicitly disabled."""
        if self.config.allow_private_hosts or url_is_public(url):
            return True
        self.stats.blocked += 1
        logger.warning("blocked non-public address: %s", url)
        return False

    def _site_ok(self, url: str) -> bool:
        return not self.config.same_domain or _site(host_of(url)) in self._allowed_sites

    def _enqueue(self, url: str, depth: int) -> None:
        normalized = normalize_url(url)
        if normalized is None or normalized in self._seen or not self._site_ok(normalized):
            return
        if self.stats.scheduled >= self.config.max_pages or not self._public_ok(normalized):
            return
        self._seen.add(normalized)
        self.stats.scheduled += 1
        self._queue.put_nowait((normalized, depth))

    async def _worker(self) -> None:
        while True:
            url, depth = await self._queue.get()
            try:
                await self._process(url, depth)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("unexpected error while processing %s", url)
                self.stats.errors += 1
            finally:
                self._queue.task_done()

    # ---- fetching ---------------------------------------------------------------

    async def _polite_wait(self, host: str, delay: float) -> None:
        loop = asyncio.get_running_loop()
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._next_allowed.get(host, 0.0) - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_allowed[host] = loop.time() + delay

    async def _validate_redirect(self, target: str) -> bool:
        """Policy for a redirect hop, evaluated BEFORE the target is requested."""
        if not self._public_ok(target) or not self._site_ok(target):
            logger.info("redirect target not permitted: %s", target)
            return False
        if self.config.respect_robots and not await self.robots.allowed(target):
            self.stats.robots_blocked += 1
            logger.info("redirect target blocked by robots.txt: %s", target)
            return False
        return True

    async def _fetch_with_retries(self, url: str, delay: float) -> FetchResult | None:
        attempts = self.config.retries + 1
        for attempt in range(attempts):
            await self._polite_wait(host_of(url), delay)
            result = await self.fetcher.fetch(url, self._validate_redirect)
            retryable = result is None or result.status >= 500 or result.status == 429
            if not retryable or attempt == attempts - 1:
                return result
            self.stats.retries += 1
            await asyncio.sleep(self.config.retry_backoff * 2**attempt)
        return None  # unreachable

    async def _process(self, url: str, depth: int) -> None:
        cfg = self.config
        delay = cfg.delay
        if cfg.respect_robots:
            if not await self.robots.allowed(url):
                self.stats.robots_blocked += 1
                logger.info("blocked by robots.txt: %s", url)
                return
            delay = max(delay, await self.robots.crawl_delay(url) or 0.0)

        try:
            result = await self._fetch_with_retries(url, delay)
        except RedirectBlocked:
            self.stats.skipped += 1
            return
        if result is None:
            self.stats.errors += 1
            return
        if result.status in (404, 410):
            if self.index.remove_document(url):
                self.stats.removed += 1
            self.stats.errors += 1
            return
        if result.status != 200:
            self.stats.errors += 1
            return
        self.stats.fetched += 1

        final_url = normalize_url(result.url) or url
        if final_url != url:
            if final_url in self._seen:  # already scheduled or crawled under its own name
                self.stats.skipped += 1
                return
            self._seen.add(final_url)
        if "html" not in result.content_type.lower():
            return
        if result.truncated:
            self.stats.too_large += 1
            logger.info("response too large, not indexed: %s", final_url)
            return

        page = parse_html(result.body, final_url)
        if page.noindex:
            self.stats.skipped += 1
            if self.index.remove_document(final_url):
                self.stats.removed += 1
        elif self.index.add_document(final_url, page.title, page.text) is None:
            self.stats.skipped += 1
        else:
            self.stats.indexed += 1
            logger.info("indexed (depth %d): %s", depth, final_url)

        if depth < cfg.max_depth and not page.nofollow:
            for link in page.links:
                self._enqueue(link, depth + 1)
