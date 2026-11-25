"""HTTP-level tests that run the real HttpFetcher against local aiohttp servers.

They need aiohttp (a runtime dependency, so CI always has it) and are skipped when it is
not installed. Servers bind to 127.0.0.1, so most tests set allow_private_hosts=True; one
test checks that the default SSRF guard refuses the same server.
"""

import asyncio
import unittest

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None

from pysearch.fetcher import HttpFetcher, RedirectBlocked
from pysearch.robots import RobotsCache

UA = "TestBot/1.0"


async def serve(routes, hits):
    """Start a local server; every request path is appended to `hits`."""

    @web.middleware
    async def record(request, handler):
        hits.append(request.path)
        return await handler(request)

    app = web.Application(middlewares=[record])
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


def page(text="<html><body>ok</body></html>", status=200):
    async def handler(request):
        return web.Response(text=text, status=status, content_type="text/html")

    return handler


def redirect(location):
    async def handler(request):
        raise web.HTTPFound(location)

    return handler


async def allow(_url):
    return True


async def deny(_url):
    return False


@unittest.skipIf(web is None, "aiohttp is not installed")
class HttpFetcherTests(unittest.TestCase):
    def fetcher(self, **kw):
        kw.setdefault("allow_private_hosts", True)
        return HttpFetcher(UA, timeout=5, **kw)

    def test_plain_fetch_and_redirect_followed_when_validated(self):
        async def run():
            hits = []
            runner, base = await serve([
                web.get("/start", redirect("/end")),
                web.get("/end", page("<p>arrived</p>")),
            ], hits)
            f = self.fetcher()
            try:
                seen = []

                async def validate(url):
                    seen.append(url)
                    return True

                result = await f.fetch(base + "/start", validate)
                return result, seen, base, hits
            finally:
                await f.close()
                await runner.cleanup()

        result, seen, base, hits = asyncio.run(run())
        self.assertEqual(result.url, base + "/end")
        self.assertIn("arrived", result.body)
        self.assertEqual(seen, [base + "/end"])
        self.assertEqual(hits, ["/start", "/end"])

    def test_vetoed_redirect_target_is_never_requested(self):
        async def run():
            target_hits, origin_hits = [], []
            target_runner, target_base = await serve([web.get("/secret", page())], target_hits)
            origin_runner, origin_base = await serve([web.get("/go", redirect(target_base + "/secret"))], origin_hits)
            f = self.fetcher()
            try:
                with self.assertRaises(RedirectBlocked):
                    await f.fetch(origin_base + "/go", deny)
                return target_hits, origin_hits
            finally:
                await f.close()
                await origin_runner.cleanup()
                await target_runner.cleanup()

        target_hits, origin_hits = asyncio.run(run())
        self.assertEqual(origin_hits, ["/go"])
        self.assertEqual(target_hits, [])  # the policy veto happened before any request

    def test_redirect_loop_gives_up(self):
        async def run():
            hits = []

            runner, base = await serve([web.get("/loop", redirect("/loop"))], hits)
            f = self.fetcher(max_redirects=3)
            try:
                return await f.fetch(base + "/loop", allow), hits
            finally:
                await f.close()
                await runner.cleanup()

        result, hits = asyncio.run(run())
        self.assertIsNone(result)
        self.assertEqual(len(hits), 4)  # first request plus three redirects

    def test_oversized_body_is_flagged_not_silently_truncated(self):
        async def run():
            hits = []

            async def big(request):
                response = web.StreamResponse(headers={"Content-Type": "text/html"})  # no Content-Length
                await response.prepare(request)
                for _ in range(20):
                    await response.write(b"x" * 100)
                await response.write_eof()
                return response

            runner, base = await serve([web.get("/big", big), web.get("/small", page())], hits)
            f = self.fetcher(max_bytes=500)
            try:
                return await f.fetch(base + "/big"), await f.fetch(base + "/small")
            finally:
                await f.close()
                await runner.cleanup()

        big, small = asyncio.run(run())
        self.assertTrue(big.truncated)
        self.assertLessEqual(len(big.body), 500)
        self.assertFalse(small.truncated)

    def test_declared_content_length_over_limit_is_flagged(self):
        async def run():
            runner, base = await serve([web.get("/big", page("y" * 2000))], [])
            f = self.fetcher(max_bytes=500)
            try:
                return await f.fetch(base + "/big")
            finally:
                await f.close()
                await runner.cleanup()

        self.assertTrue(asyncio.run(run()).truncated)

    def test_ssrf_guard_refuses_local_server_by_default(self):
        async def run():
            hits = []
            runner, base = await serve([web.get("/", page())], hits)
            f = HttpFetcher(UA, timeout=5)  # allow_private_hosts defaults to False
            try:
                return await f.fetch(base + "/"), hits
            finally:
                await f.close()
                await runner.cleanup()

        result, hits = asyncio.run(run())
        self.assertIsNone(result)
        self.assertEqual(hits, [])


@unittest.skipIf(web is None, "aiohttp is not installed")
class RobotsOverHttpTests(unittest.TestCase):
    def run_robots(self, routes, checks):
        async def run():
            hits = []
            runner, base = await serve(routes, hits)
            f = HttpFetcher(UA, timeout=5, allow_private_hosts=True)
            cache = RobotsCache(f, UA)
            try:
                results = [await cache.allowed(base + path) for path in checks]
                return results, hits
            finally:
                await f.close()
                await runner.cleanup()

        return asyncio.run(run())

    def test_rules_are_fetched_once_and_cached(self):
        routes = [web.get("/robots.txt", page("User-agent: *\nDisallow: /private\n"))]
        results, hits = self.run_robots(routes, ["/ok", "/private/x", "/also-ok"])
        self.assertEqual(results, [True, False, True])
        self.assertEqual(hits.count("/robots.txt"), 1)

    def test_server_error_means_disallow_all(self):
        routes = [web.get("/robots.txt", page("", status=503))]
        results, _ = self.run_robots(routes, ["/a", "/b"])
        self.assertEqual(results, [False, False])

    def test_missing_robots_means_allow_all(self):
        results, _ = self.run_robots([], ["/a"])  # aiohttp answers 404
        self.assertEqual(results, [True])


if __name__ == "__main__":
    unittest.main()
