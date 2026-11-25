"""Offline search-quality evaluation: Precision@k, Recall@k, MRR and NDCG@k.

Judgments file (JSON): a list of objects, each with a query and its relevant URLs.
`relevant` is either a list of URLs (binary relevance) or a {url: grade} object for
graded relevance (higher = better; used by NDCG):

    [
      {"query": "python asyncio tutorial", "relevant": {"https://a.com/asyncio": 2, "https://b.com/intro": 1}},
      {"query": "sqlite wal mode", "relevant": ["https://c.com/wal"]}
    ]
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pysearch.search import SearchEngine


def precision_at_k(ranked: Sequence[str], relevant: dict[str, int], k: int) -> float:
    return sum(1 for url in ranked[:k] if relevant.get(url, 0) > 0) / k


def recall_at_k(ranked: Sequence[str], relevant: dict[str, int], k: int) -> float:
    total = sum(1 for grade in relevant.values() if grade > 0)
    return sum(1 for url in ranked[:k] if relevant.get(url, 0) > 0) / total if total else 0.0


def reciprocal_rank(ranked: Sequence[str], relevant: dict[str, int]) -> float:
    for rank, url in enumerate(ranked, 1):
        if relevant.get(url, 0) > 0:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevant: dict[str, int], k: int) -> float:
    def dcg(grades: Sequence[int]) -> float:
        return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(grades))

    ideal = dcg(sorted((g for g in relevant.values() if g > 0), reverse=True)[:k])
    actual = dcg([relevant.get(url, 0) for url in ranked[:k]])
    return actual / ideal if ideal else 0.0


@dataclass
class QueryJudgment:
    query: str
    relevant: dict[str, int]


@dataclass
class EvalReport:
    k: int
    queries: int
    precision: float
    recall: float
    mrr: float
    ndcg: float
    mean_latency_ms: float
    per_query: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"queries={self.queries}  P@{self.k}={self.precision:.3f}  "
            f"R@{self.k}={self.recall:.3f}  MRR={self.mrr:.3f}  "
            f"NDCG@{self.k}={self.ndcg:.3f}  latency={self.mean_latency_ms:.2f} ms"
        )


def load_judgments(path: str | Path) -> list[QueryJudgment]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    judgments = []
    for item in data:
        relevant = item["relevant"]
        if isinstance(relevant, list):
            relevant = dict.fromkeys(relevant, 1)
        judgments.append(QueryJudgment(item["query"], {str(u): int(g) for u, g in relevant.items()}))
    return judgments


def evaluate(engine: SearchEngine, judgments: Sequence[QueryJudgment], k: int = 10) -> EvalReport:
    if k < 1:
        raise ValueError("k must be >= 1")
    if not judgments:
        raise ValueError("no judgments to evaluate")
    rows = []
    for j in judgments:
        response = engine.search(j.query, limit=k)
        ranked = [r.url for r in response.results]
        rows.append(
            {
                "query": j.query,
                "precision": precision_at_k(ranked, j.relevant, k),
                "recall": recall_at_k(ranked, j.relevant, k),
                "rr": reciprocal_rank(ranked, j.relevant),
                "ndcg": ndcg_at_k(ranked, j.relevant, k),
                "latency_ms": response.took_ms,
            }
        )
    n = len(rows)
    mean = lambda key: sum(r[key] for r in rows) / n  # noqa: E731
    return EvalReport(
        k=k,
        queries=n,
        precision=mean("precision"),
        recall=mean("recall"),
        mrr=mean("rr"),
        ndcg=mean("ndcg"),
        mean_latency_ms=mean("latency_ms"),
        per_query=rows,
    )
