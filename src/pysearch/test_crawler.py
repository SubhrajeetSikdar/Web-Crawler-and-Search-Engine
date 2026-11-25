import asyncio
import unittest

from pysearch.config import CrawlConfig
from pysearch.crawler import Crawler
from pysearch.fetcher import FetchResult, RedirectBlocked
from pysearch.index import Index
from pysearch.search import SearchEngine


def page(title, body, *links):
    anchors = "".join(f'<a href="{href}">link</a>' for href in links)
    return f"<html><head><title>{title}</title></head><body><p>{body}</p>{anchors}</body></html>"


class Redirect:
    def __init__(self, target):
        self.target = target


class Status:
    def __init__(self, status):
        self.status = status


class Truncated:
    def __init__(self, body):
        self.body = body


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    async def fetch(self, url, validate=None):
        self.requested.append(url)
        item = self.pages.get(url)
        if isinstance(item, Redirect):
            # like HttpFetcher: the redirect target is validated BEFORE it is requested
            if validate is not None and not await validate(item.target):
                raise RedirectBlocked(item.target)
            self.requested.append(item.target)
            return FetchResult(item.target, 200, "text/html", self.pages[item.target])
        if isinstance(item, Truncated):
            return FetchResult(url, 200, "text/html", item.body, truncated=True)
        if isinstance(item, Status):
            return FetchResult(url, item.status, "text/plain", "")
        if isinstance(item, str):
            ctype = "text/plain" if url.endswith("robots.txt") else "text/html; charset=utf-8"
            return FetchResult(url, 200, ctype, item)
        return FetchResult(url, 404, "text/plain", "")

    async def close(self):
        pass


SITE = {
    "https://example.com/robots.txt": "User-agent: *\nDisallow: /private/\n",
    "https://example.com/": page("Home", "Welcome to the crawler demo", "/a", "/b#top", "/private/x", "https://other.com/"),
    "https://example.com/a": page("Page A", "Alpha content about asyncio", "/deep", "/"),
    "https://example.com/b": page("Page B", "Bravo content about sqlite"),
    "https://example.com/deep": page("Deep", "Deeply nested zebra page"),
    "https://example.com/private/x": page("Secret", "Hidden aardvark"),
    "https://other.com/": page("Other", "External site"),
}


def run_crawl(site=None, seeds=("https://example.com/",), index=None, **overrides):
    index = index or Index(":memory:")
    fetcher = FakeFetcher(dict(SITE if site is None else site))
    overrides.setdefault("retry_backoff", 0)
    config = CrawlConfig(delay=0, concurrency=3, **overrides)
    stats = asyncio.run(Crawler(config, index, fetcher).crawl(list(seeds)))
    return index, fetcher, stats


class CrawlerTests(unittest.TestCase):
    def test_crawls_and_indexes_site(self):
        index, fetcher, stats = run_crawl()
        engine = SearchEngine(index)
        self.assertEqual(stats.indexed, 4)
        self.assertEqual(engine.search("zebra").results[0].url, "https://example.com/deep")
        self.assertEqual(engine.search("sqlite").results[0].title, "Page B")

    def test_respects_robots_and_same_domain(self):
        index, fetcher, stats = run_crawl()
        self.assertNotIn("https://example.com/private/x", fetcher.requested)
        self.assertNotIn("https://other.com/", fetcher.requested)
        self.assertEqual(stats.robots_blocked, 1)
        self.assertEqual(SearchEngine(index).search("aardvark").total, 0)

    def test_ignore_robots_and_follow_external(self):
        index, fetcher, stats = run_crawl(respect_robots=False, same_domain=False)
        self.assertIn("https://example.com/private/x", fetcher.requested)
        self.assertIn("https://other.com/", fetcher.requested)

    def test_depth_limit(self):
        index, fetcher, stats = run_crawl(max_depth=1)
        self.assertNotIn("https://example.com/deep", fetcher.requested)
        self.assertEqual(SearchEngine(index).search("zebra").total, 0)

    def test_page_cap_and_no_duplicate_fetches(self):
        index, fetcher, stats = run_crawl(max_pages=2)
        self.assertEqual(stats.scheduled, 2)
        pages = [u for u in fetcher.requested if not u.endswith("robots.txt")]
        self.assertEqual(len(pages), len(set(pages)))


ROBOTS_OK = "User-agent: *\nDisallow:\n"


class SsrfTests(unittest.TestCase):
    def test_internal_seeds_are_never_requested(self):
        seeds = ["http://169.254.169.254/latest/meta-data/", "http://localhost:8080/admin", "http://10.0.0.1/"]
        _, fetcher, stats = run_crawl(site={}, seeds=seeds)
        self.assertEqual(fetcher.requested, [])
        self.assertEqual(stats.scheduled, 0)

    def test_links_to_internal_hosts_are_not_followed(self):
        site = {"https://example.com/": page("Home", "hello world", "http://127.0.0.1/", "http://192.168.0.1/x")}
        _, fetcher, stats = run_crawl(site=site, same_domain=False, respect_robots=False)
        self.assertEqual(fetcher.requested, ["https://example.com/"])
        self.assertEqual(stats.blocked, 2)

    def test_redirect_to_internal_host_is_not_indexed(self):
        site = {
            "https://example.com/": page("Home", "hello world", "/go"),
            "https://example.com/go": Redirect("http://169.254.169.254/latest/"),
            "http://169.254.169.254/latest/": page("Metadata", "secret credentials"),
        }
        index, fetcher, stats = run_crawl(site=site, respect_robots=False)
        self.assertNotIn("http://169.254.169.254/latest/", fetcher.requested)  # never requested
        self.assertEqual(SearchEngine(index).search("credentials").total, 0)
        self.assertEqual(stats.blocked, 1)

    def test_redirect_to_other_domain_is_not_indexed(self):
        site = {
            "https://example.com/": page("Home", "hello world", "/go"),
            "https://example.com/go": Redirect("https://evil.com/page"),
            "https://evil.com/page": page("Evil", "offsite zebra"),
        }
        index, fetcher, stats = run_crawl(site=site, respect_robots=False)
        self.assertNotIn("https://evil.com/page", fetcher.requested)  # never requested
        self.assertEqual(stats.skipped, 1)
        self.assertEqual(SearchEngine(index).search("zebra").total, 0)
        self.assertEqual(SearchEngine(index).search("hello").total, 1)

    def test_redirect_to_www_variant_is_allowed(self):
        site = {
            "https://example.com/": Redirect("https://www.example.com/"),
            "https://www.example.com/": page("Home", "welcome aboard"),
        }
        index, _, _ = run_crawl(site=site, respect_robots=False)
        self.assertEqual(SearchEngine(index).search("welcome").results[0].url, "https://www.example.com/")

    def test_redirect_target_robots_is_checked(self):
        site = {
            "https://example.com/": Redirect("https://www.example.com/secret"),
            "https://www.example.com/secret": page("Secret", "hidden aardvark"),
            "https://www.example.com/robots.txt": "User-agent: *\nDisallow: /secret\n",
            "https://example.com/robots.txt": ROBOTS_OK,
        }
        index, fetcher, stats = run_crawl(site=site)
        self.assertNotIn("https://www.example.com/secret", fetcher.requested)  # never requested
        self.assertEqual(SearchEngine(index).search("aardvark").total, 0)
        self.assertEqual(stats.robots_blocked, 1)

    def test_allow_private_hosts_switch_for_local_development(self):
        site = {"http://localhost:8000/": page("Local", "dev server page")}
        index, fetcher, _ = run_crawl(site=site, seeds=["http://localhost:8000/"], respect_robots=False, allow_private_hosts=True)
        self.assertEqual(SearchEngine(index).search("dev").total, 1)


class RobotsFailureTests(unittest.TestCase):
    def test_robots_server_error_blocks_the_host(self):
        site = {"https://example.com/robots.txt": Status(503), "https://example.com/": page("Home", "hello world")}
        index, fetcher, stats = run_crawl(site=site, retries=0)
        self.assertEqual(stats.robots_blocked, 1)
        self.assertEqual(index.stats().documents, 0)
        self.assertNotIn("https://example.com/", fetcher.requested)

    def test_missing_robots_allows_crawling(self):
        site = {"https://example.com/": page("Home", "hello world")}  # robots.txt -> 404
        index, _, _ = run_crawl(site=site)
        self.assertEqual(index.stats().documents, 1)


class ReliabilityTests(unittest.TestCase):
    def test_zero_concurrency_is_rejected_instead_of_hanging(self):
        with self.assertRaises(ValueError):
            CrawlConfig(concurrency=0)

    def test_transient_errors_are_retried(self):
        class Flaky(FakeFetcher):
            async def fetch(self, url, validate=None):
                result = await super().fetch(url, validate)
                if url == "https://example.com/" and self.requested.count(url) < 3:
                    return FetchResult(url, 503, "text/plain", "")
                return result

        index = Index(":memory:")
        fetcher = Flaky({"https://example.com/": page("Home", "finally works")})
        config = CrawlConfig(delay=0, retries=2, retry_backoff=0, respect_robots=False)
        stats = asyncio.run(Crawler(config, index, fetcher).crawl(["https://example.com/"]))
        self.assertEqual((stats.indexed, stats.retries, stats.errors), (1, 2, 0))

    def test_gives_up_after_retries(self):
        site = {"https://example.com/": Status(503)}
        _, fetcher, stats = run_crawl(site=site, retries=1, respect_robots=False)
        self.assertEqual((fetcher.requested.count("https://example.com/"), stats.errors), (2, 1))

    def test_page_that_turns_noindex_or_404_is_removed(self):
        index = Index(":memory:")
        index.add_document("https://example.com/", "Home", "stale content here")
        index.add_document("https://example.com/gone", "Gone", "another stale page")
        site = {
            "https://example.com/": '<meta name="robots" content="noindex"><p>now hidden</p><a href="/gone">g</a>',
            "https://example.com/gone": Status(410),
        }
        _, _, stats = run_crawl(site=site, index=index, respect_robots=False)
        self.assertEqual(index.stats().documents, 0)
        self.assertEqual(stats.removed, 2)


class ResponseSizeTests(unittest.TestCase):
    def test_truncated_html_is_not_indexed_or_followed(self):
        site = {
            "https://example.com/": page("Home", "hello world", "/big"),
            "https://example.com/big": Truncated("<html><title>Big</title><p>half a page <a href='/never'>x"),
            "https://example.com/never": page("Never", "should not be crawled"),
        }
        index, fetcher, stats = run_crawl(site=site, respect_robots=False)
        self.assertEqual(stats.too_large, 1)
        self.assertEqual(index.stats().documents, 1)
        self.assertNotIn("https://example.com/never", fetcher.requested)


class ReuseTests(unittest.TestCase):
    def make(self):
        index = Index(":memory:")
        fetcher = FakeFetcher({"https://example.com/": page("Home", "hello world", "/a"), "https://example.com/a": page("A", "alpha page")})
        return index, Crawler(CrawlConfig(delay=0, concurrency=2, respect_robots=False), index, fetcher)

    def test_same_crawler_can_run_twice_with_fresh_stats(self):
        index, crawler = self.make()
        first = asyncio.run(crawler.crawl(["https://example.com/"]))
        second = asyncio.run(crawler.crawl(["https://example.com/"]))
        self.assertIsNot(first, second)
        self.assertEqual((first.scheduled, first.indexed), (2, 2))
        self.assertEqual((second.scheduled, second.fetched), (2, 2))  # really re-crawled
        self.assertEqual(index.stats().documents, 2)

    def test_concurrent_runs_on_one_crawler_are_rejected(self):
        _, crawler = self.make()

        async def both():
            return await asyncio.gather(
                crawler.crawl(["https://example.com/"]),
                crawler.crawl(["https://example.com/"]),
                return_exceptions=True,
            )

        results = asyncio.run(both())
        self.assertEqual(sum(isinstance(r, RuntimeError) for r in results), 1)
        self.assertEqual(sum(not isinstance(r, Exception) for r in results), 1)
        asyncio.run(crawler.crawl(["https://example.com/"]))  # usable again afterwards


if __name__ == "__main__":
    unittest.main()
