import unittest

from pysearch.tokenizer import stem, tokenize


class TokenizerTests(unittest.TestCase):
    def test_lowercases_and_drops_stopwords(self):
        self.assertEqual(tokenize("The Quick BROWN fox"), ["quick", "brown", "fox"])

    def test_stemming_is_consistent_across_forms(self):
        for form in ("crawl", "crawls", "crawled", "crawling"):
            self.assertEqual(stem(form), "crawl")
        self.assertEqual(stem("running"), "run")
        self.assertEqual(stem("indexes"), "index")
        self.assertEqual(stem("pages"), "page")

    def test_short_and_numeric_tokens_untouched(self):
        self.assertEqual(stem("bus"), "bus")
        self.assertEqual(stem("2024"), "2024")

    def test_punctuation_and_unicode(self):
        self.assertEqual(tokenize("hello, world! café_au_lait"), ["hello", "world", "café", "au", "lait"])


if __name__ == "__main__":
    unittest.main()
