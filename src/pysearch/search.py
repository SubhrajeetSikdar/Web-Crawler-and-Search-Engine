from __future__ import annotations

import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

from pysearch.index import Index
from pysearch.tokenizer import STOPWORDS, stem, tokenize

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def bm25_term_score(
    tf: int, df: int, n_docs: int, doc_len: int, avg_len: float, k1: float = 1.5, b: float = 0.75
) -> float:
    """Okapi BM25 contribution of one query term to one document."""
    idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
    norm = 1.0 - b + b * (doc_len / avg_len if avg_len else 1.0)
    return idf * (tf * (k1 + 1.0)) / (tf + k1 * norm)


@dataclass
class Snippet:
    text: str
    highlights: list[tuple[int, int]] = field(default_factory=list)  # UTF-16 offsets in text


@dataclass
class SearchResult:
    doc_id: int
    url: str
    title: str
    snippet: str
    highlights: list[tuple[int, int]]
    score: float


@dataclass
class SearchResponse:
    query: str
    total: int
    took_ms: float
    results: list[SearchResult] = field(default_factory=list)


def _utf16(text: str) -> int:
    """Length in UTF-16 code units, which is how JavaScript indexes strings."""
    return len(text.encode("utf-16-le")) // 2


def make_snippet(content: str, query: str, width: int = 220) -> Snippet:
    """Window of text around the first match, plus highlight spans for every match.

    Matching uses the same normalisation as the index (lowercase, stopwords, stemming),
    so a query for "crawling" finds and highlights "crawl", "crawled" or "crawlers".
    """
    terms = set(tokenize(query))
    hits = [
        (m.start(), m.end())
        for m in _WORD.finditer(content)
        if (w := m.group().lower()) not in STOPWORDS and stem(w) in terms
    ]
    if not hits:
        return Snippet(content[:width] + ("…" if len(content) > width else ""))

    start = max(0, hits[0][0] - width // 3)
    if start > 0:  # begin on a word boundary
        space = content.rfind(" ", 0, start)
        start = space + 1 if space != -1 else start
    end = min(len(content), start + width)
    if end < len(content):  # finish on a word boundary
        space = content.find(" ", end)
        end = space if space != -1 and space - end < 20 else end

    prefix = "…" if start > 0 else ""
    window = content[start:end]
    text = prefix + window + ("…" if end < len(content) else "")
    spans = [
        (_utf16(text[: len(prefix) + s - start]), _utf16(text[: len(prefix) + min(e, end) - start]))
        for s, e in hits
        if s >= start and s < end
    ]
    return Snippet(text, spans)


class SearchEngine:
    def __init__(self, index: Index, k1: float = 1.5, b: float = 0.75):
        self.index = index
        self.k1 = k1
        self.b = b

    def search(self, query: str, limit: int = 10, offset: int = 0) -> SearchResponse:
        started = time.perf_counter()
        terms = list(dict.fromkeys(tokenize(query)))
        stats = self.index.stats()
        scores: dict[int, float] = defaultdict(float)
        matched: dict[int, int] = defaultdict(int)

        for term in terms:
            rows = self.index.postings(term)
            df = len(rows)
            for doc_id, tf, doc_len in rows:
                scores[doc_id] += bm25_term_score(
                    tf, df, stats.documents, doc_len, stats.avg_length, self.k1, self.b
                )
                matched[doc_id] += 1

        # Reward documents that contain every query term.
        for doc_id, count in matched.items():
            if len(terms) > 1 and count == len(terms):
                scores[doc_id] *= 1.25

        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        page = ranked[offset : offset + limit]
        docs = self.index.get_documents([doc_id for doc_id, _ in page])
        results = []
        for doc_id, score in page:
            doc = docs.get(doc_id)
            if doc is None:
                continue
            snippet = make_snippet(doc.content, query)
            results.append(
                SearchResult(
                    doc_id=doc_id,
                    url=doc.url,
                    title=doc.title or doc.url,
                    snippet=snippet.text,
                    highlights=snippet.highlights,
                    score=round(score, 4),
                )
            )
        return SearchResponse(
            query=query,
            total=len(ranked),
            took_ms=round((time.perf_counter() - started) * 1000, 2),
            results=results,
        )
