"""Unit tests for HttpFetcher body handling using a fake response (no aiohttp needed)."""

import asyncio
import unittest

from pysearch.fetcher import HttpFetcher


class FakeContent:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, n):
        for chunk in self.chunks:
            yield chunk  # arbitrary small chunks, like a real network stream


class FakeResponse:
    status = 200
    charset = "utf-8"

    def __init__(self, chunks, headers=None):
        self.content = FakeContent(chunks)
        self.headers = {"Content-Type": "text/html", **(headers or {})}


def read(chunks, max_bytes, headers=None):
    fetcher = HttpFetcher("bot", max_bytes=max_bytes)
    return asyncio.run(fetcher._read(FakeResponse(chunks, headers), "https://x.com/"))


class ReadTests(unittest.TestCase):
    def test_body_split_across_many_chunks_is_complete(self):
        result = read([b"<html>", b"<p>he", b"llo wor", b"ld</p></html>"], 1000)
        self.assertEqual(result.body, "<html><p>hello world</p></html>")
        self.assertFalse(result.truncated)

    def test_exactly_at_limit_is_not_truncated(self):
        self.assertFalse(read([b"x" * 50, b"y" * 50], 100).truncated)

    def test_over_limit_is_flagged_and_cut(self):
        result = read([b"x" * 60, b"y" * 60, b"z" * 60], 100)
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.body), 100)

    def test_declared_length_over_limit_short_circuits(self):
        result = read([b"never read"], 100, {"Content-Length": "5000"})
        self.assertTrue(result.truncated)
        self.assertEqual(result.body, "")

    def test_unknown_charset_falls_back_to_utf8(self):
        response = FakeResponse([b"caf\xc3\xa9"])
        response.charset = "no-such-charset"
        result = asyncio.run(HttpFetcher("bot")._read(response, "https://x.com/"))
        self.assertEqual(result.body, "café")


if __name__ == "__main__":
    unittest.main()
