import json
import math
import tempfile
import unittest
from pathlib import Path

from pysearch.evaluation import (
    QueryJudgment, evaluate, load_judgments, ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank,
)
from pysearch.index import Index
from pysearch.search import SearchEngine


class MetricTests(unittest.TestCase):
    ranked = ["a", "b", "c", "d"]
    relevant = {"a": 1, "c": 1}

    def test_precision_recall_mrr(self):
        self.assertAlmostEqual(precision_at_k(self.ranked, self.relevant, 3), 2 / 3)
        self.assertAlmostEqual(recall_at_k(self.ranked, self.relevant, 1), 0.5)
        self.assertEqual(recall_at_k(self.ranked, self.relevant, 3), 1.0)
        self.assertEqual(reciprocal_rank(self.ranked, self.relevant), 1.0)
        self.assertEqual(reciprocal_rank(["x", "y", "c"], self.relevant), 1 / 3)
        self.assertEqual(reciprocal_rank(["x"], self.relevant), 0.0)

    def test_ndcg_hand_computed(self):
        expected = (1 + 1 / math.log2(4)) / (1 + 1 / math.log2(3))
        self.assertAlmostEqual(ndcg_at_k(self.ranked, self.relevant, 3), expected)

    def test_ndcg_perfect_and_graded(self):
        self.assertEqual(ndcg_at_k(["a", "c"], self.relevant, 2), 1.0)
        graded = {"a": 2, "b": 1}
        self.assertEqual(ndcg_at_k(["a", "b"], graded, 2), 1.0)
        self.assertLess(ndcg_at_k(["b", "a"], graded, 2), 1.0)

    def test_no_relevant_documents_scores_zero(self):
        self.assertEqual(ndcg_at_k(self.ranked, {}, 3), 0.0)
        self.assertEqual(recall_at_k(self.ranked, {}, 3), 0.0)


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.index = Index(":memory:")
        self.index.add_document("https://a.com/py", "Python asyncio", "Async programming in Python with asyncio.")
        self.index.add_document("https://a.com/cook", "Pasta", "Cooking pasta with tomato sauce.")
        self.engine = SearchEngine(self.index)

    def test_end_to_end_report(self):
        report = evaluate(self.engine, [
            QueryJudgment("python asyncio", {"https://a.com/py": 1}),
            QueryJudgment("pasta", {"https://a.com/cook": 1}),
            QueryJudgment("unknownterm", {"https://a.com/py": 1}),
        ], k=5)
        self.assertEqual(report.queries, 3)
        self.assertAlmostEqual(report.mrr, 2 / 3)
        self.assertAlmostEqual(report.recall, 2 / 3)
        self.assertIn("MRR=0.667", report.summary())

    def test_load_judgments_accepts_lists_and_grades(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            path.write_text(json.dumps([
                {"query": "q1", "relevant": ["https://a.com/py"]},
                {"query": "q2", "relevant": {"https://a.com/cook": 2}},
            ]))
            loaded = load_judgments(path)
        self.assertEqual(loaded[0].relevant, {"https://a.com/py": 1})
        self.assertEqual(loaded[1].relevant, {"https://a.com/cook": 2})

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            evaluate(self.engine, [], k=5)
        with self.assertRaises(ValueError):
            evaluate(self.engine, [QueryJudgment("q", {})], k=0)


if __name__ == "__main__":
    unittest.main()
