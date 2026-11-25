import unittest

from pysearch.parser import parse_html
from pysearch.urls import normalize_url


class UrlTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(normalize_url("HTTP://Example.COM:80/a#frag"), "http://example.com/a")
        self.assertEqual(normalize_url("https://example.com"), "https://example.com/")
        self.assertEqual(normalize_url("https://example.com:8443/x?y=1"), "https://example.com:8443/x?y=1")

    def test_relative_resolution(self):
        self.assertEqual(normalize_url("../b", "https://e.com/x/y/"), "https://e.com/x/b")

    def test_rejected(self):
        for bad in ("mailto:a@b.c", "javascript:void(0)", "ftp://e.com/", "https://e.com/pic.JPG", "https://e.com:bad/"):
            self.assertIsNone(normalize_url(bad), bad)


HTML = """<html><head><title> My  Page </title><base href="https://e.com/docs/">
<meta name="robots" content="noindex, nofollow"><style>.x{}</style></head>
<body><h1>Hello</h1><script>var secret = 1;</script><p>World &amp; more</p>
<a href="intro">Intro</a>
<a href="/abs">Abs</a>
<a href="mailto:x@y.z">Mail</a>
<a href="intro">Dup</a>
<a href="/skip" rel="nofollow">NF</a></body></html>"""


class ParserTests(unittest.TestCase):
    def test_extraction(self):
        page = parse_html(HTML, "https://e.com/")
        self.assertEqual(page.title, "My Page")
        self.assertEqual(page.text, "Hello World & more Intro Abs Mail Dup NF")
        self.assertNotIn("secret", page.text)
        self.assertEqual(page.links, ["https://e.com/docs/intro", "https://e.com/abs"])
        self.assertTrue(page.noindex and page.nofollow)


if __name__ == "__main__":
    unittest.main()
