import asyncio
import unittest

from pysearch.config import CrawlConfig
from pysearch.fetcher import FetchResult
from pysearch.robots import FAILURE_TTL, RobotsCache


class StubFetcher:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    async def fetch(self, url):
        self.calls += 1
        r = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        return None if r is None else FetchResult(url, r[0], "text/plain", r[1])

    async def close(self):
        pass


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def allowed(cache, url="https://x.com/page"):
    return asyncio.run(cache.allowed(url))


class RobotsTests(unittest.TestCase):
    def test_rules_are_obeyed(self):
        cache = RobotsCache(StubFetcher((200, "User-agent: *\nDisallow: /private/\nCrawl-delay: 2")), "bot")
        self.assertFalse(allowed(cache, "https://x.com/private/a"))
        self.assertTrue(allowed(cache, "https://x.com/public"))
        self.assertEqual(asyncio.run(cache.crawl_delay("https://x.com/")), 2.0)

    def test_missing_robots_4xx_allows_everything(self):
        for status in (404, 403, 410):
            self.assertTrue(allowed(RobotsCache(StubFetcher((status, "")), "bot")), status)

    def test_server_error_and_network_failure_disallow_everything(self):
        for response in ((503, ""), (500, ""), (429, ""), None):
            self.assertFalse(allowed(RobotsCache(StubFetcher(response), "bot")), response)

    def test_unreachable_robots_is_retried_after_failure_ttl(self):
        clock, fetcher = Clock(), StubFetcher((503, ""), (200, "User-agent: *\nDisallow:"))
        cache = RobotsCache(fetcher, "bot", clock=clock)
        self.assertFalse(allowed(cache))
        self.assertFalse(allowed(cache))
        self.assertEqual(fetcher.calls, 1)  # cached while the failure is fresh
        clock.now = FAILURE_TTL + 1
        self.assertTrue(allowed(cache))
        self.assertEqual(fetcher.calls, 2)


class ConfigValidationTests(unittest.TestCase):
    def test_invalid_values_rejected(self):
        for kwargs in (
            {"concurrency": 0}, {"concurrency": -1}, {"max_pages": 0}, {"max_depth": -1},
            {"delay": -0.1}, {"timeout": 0}, {"max_bytes": 0}, {"retries": -1},
            {"retry_backoff": -1}, {"max_redirects": -1}, {"user_agent": "  "},
        ):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                CrawlConfig(**kwargs)

    def test_defaults_are_valid(self):
        self.assertEqual(CrawlConfig().concurrency, 10)
        self.assertFalse(CrawlConfig().allow_private_hosts)


if __name__ == "__main__":
    unittest.main()
