import unittest

from pysearch.index import Index
from pysearch.search import SearchEngine, bm25_term_score, make_snippet


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.index = Index(":memory:")
        self.index.add_document("https://a.com/1", "Python web crawler", "Building an async crawler in Python with asyncio.")
        self.index.add_document("https://a.com/2", "Cooking pasta", "Boil water and cook the pasta for ten minutes.")
        self.index.add_document("https://a.com/3", "Search engines", "A search engine ranks pages. Python can crawl pages.")
        self.engine = SearchEngine(self.index)

    def test_ranking_prefers_best_match(self):
        r = self.engine.search("python crawler")
        self.assertEqual(r.results[0].url, "https://a.com/1")
        self.assertEqual({x.url for x in r.results}, {"https://a.com/1", "https://a.com/3"})

    def test_stemmed_query_matches(self):
        # "crawling" stems to "crawl", which only appears in document 3's body
        self.assertEqual(self.engine.search("crawling").results[0].url, "https://a.com/3")
        self.assertEqual(self.engine.search("engines").results[0].url, "https://a.com/3")

    def test_no_match_and_stopword_only_queries(self):
        self.assertEqual(self.engine.search("zebra").total, 0)
        self.assertEqual(self.engine.search("the of and").total, 0)

    def test_pagination(self):
        r = self.engine.search("python", limit=1, offset=1)
        self.assertEqual(len(r.results), 1)
        self.assertEqual(r.total, 2)

    def test_reindex_replaces_old_content(self):
        self.index.add_document("https://a.com/2", "Gardening", "Plant tomatoes in spring.")
        self.assertEqual(self.engine.search("pasta").total, 0)
        self.assertEqual(self.engine.search("tomatoes").results[0].url, "https://a.com/2")
        self.assertEqual(self.index.stats().documents, 3)

    def test_duplicate_content_skipped(self):
        self.assertIsNone(self.index.add_document("https://mirror.com/2", "Cooking pasta", "Boil water and cook the pasta for ten minutes."))
        self.assertEqual(self.index.stats().documents, 3)

    def test_empty_page_skipped(self):
        self.assertIsNone(self.index.add_document("https://a.com/empty", "", "the and of"))

    def test_bm25_properties(self):
        rare = bm25_term_score(2, 1, 100, 50, 50)
        common = bm25_term_score(2, 90, 100, 50, 50)
        self.assertGreater(rare, common)
        self.assertGreater(bm25_term_score(5, 10, 100, 50, 50), bm25_term_score(1, 10, 100, 50, 50))
        self.assertGreater(bm25_term_score(2, 10, 100, 20, 50), bm25_term_score(2, 10, 100, 200, 50))

    def test_snippet_centres_on_match(self):
        text = "x " * 200 + "needle appears here " + "y " * 200
        snippet = make_snippet(text, "needle")
        self.assertIn("needle", snippet.text)
        self.assertTrue(snippet.text.startswith("…") and snippet.text.endswith("…"))

    def test_snippet_uses_stemmed_matching_and_highlights(self):
        text = "filler " * 80 + "we crawl pages and crawlers here " + "filler " * 80
        snippet = make_snippet(text, "crawling pages")
        self.assertIn("crawl pages", snippet.text)
        marked = [snippet.text[a:b] for a, b in snippet.highlights]
        self.assertEqual(marked, ["crawl", "pages"])

    def test_highlight_offsets_are_utf16_for_javascript(self):
        snippet = make_snippet("😀 python 😀 python", "python")
        units = snippet.text.encode("utf-16-le")
        spans = [units[a * 2 : b * 2].decode("utf-16-le") for a, b in snippet.highlights]
        self.assertEqual(spans, ["python", "python"])

    def test_search_results_carry_highlights(self):
        result = self.engine.search("crawling").results[0]
        self.assertTrue(result.highlights)
        a, b = result.highlights[0]
        self.assertEqual(result.snippet[a:b].lower(), "crawl")

    def test_no_match_falls_back_to_start_of_content(self):
        snippet = make_snippet("alpha beta gamma", "zebra")
        self.assertEqual((snippet.text, snippet.highlights), ("alpha beta gamma", []))


if __name__ == "__main__":
    unittest.main()
