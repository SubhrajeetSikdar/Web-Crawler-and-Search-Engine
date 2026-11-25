import unittest

from pysearch.index import MAX_INDEXED_CHARS, Index
from pysearch.tokenizer import tokenize
from pysearch.search import SearchEngine


class IndexRegressionTests(unittest.TestCase):
    def setUp(self):
        self.index = Index(":memory:")
        self.engine = SearchEngine(self.index)
        self.index.add_document("https://a.com/", "Old", "apples and oranges grow in orchards")
        self.index.add_document("https://b.com/", "New", "completely different bananas content")

    def test_url_that_becomes_duplicate_does_not_leave_stale_document(self):
        # Issue 1: A is updated to exactly B's content; A's old text must disappear.
        result = self.index.add_document("https://a.com/", "New", "completely different bananas content")
        self.assertIsNone(result)
        self.assertEqual(self.engine.search("apples").total, 0)
        self.assertEqual({r.url for r in self.engine.search("bananas").results}, {"https://b.com/"})
        self.assertEqual(self.index.stats().documents, 1)

    def test_url_that_becomes_empty_does_not_leave_stale_document(self):
        self.assertIsNone(self.index.add_document("https://a.com/", "", "the and of"))
        self.assertEqual(self.engine.search("apples").total, 0)

    def test_postings_are_removed_with_the_document(self):
        self.index.add_document("https://a.com/", "New", "completely different bananas content")
        self.assertEqual(self.index.postings("apple"), [])

    def test_remove_document(self):
        self.assertTrue(self.index.remove_document("https://a.com/"))
        self.assertFalse(self.index.remove_document("https://a.com/"))
        self.assertEqual(self.engine.search("apples").total, 0)

    def test_normal_reindex_still_works(self):
        self.index.add_document("https://a.com/", "Fresh", "kiwis and mangoes")
        self.assertEqual(self.engine.search("kiwis").results[0].url, "https://a.com/")
        self.assertEqual(self.engine.search("apples").total, 0)


class IndexedTextConsistencyTests(unittest.TestCase):
    def test_length_postings_and_stored_text_describe_the_same_text(self):
        index = Index(":memory:")
        text = "alpha " * (MAX_INDEXED_CHARS // 6 + 50) + "omega"
        doc_id = index.add_document("https://x.com/", "T", text)
        stored = index.get_documents([doc_id])[doc_id].content
        self.assertLessEqual(len(stored), MAX_INDEXED_CHARS)
        self.assertEqual(index.stats().avg_length, len(tokenize("T") * 3 + tokenize(stored)))
        # text beyond the limit is neither indexed nor searchable
        self.assertEqual(SearchEngine(index).search("omega").total, 0)
        self.assertEqual(SearchEngine(index).search("alpha").total, 1)


if __name__ == "__main__":
    unittest.main()
