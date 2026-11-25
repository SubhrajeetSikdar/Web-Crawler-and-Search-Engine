from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pysearch.tokenizer import tokenize

TITLE_WEIGHT = 3  # title terms count this many times in term frequency
MAX_INDEXED_CHARS = 100_000  # text beyond this is neither indexed nor stored

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY,
    url          TEXT UNIQUE NOT NULL,
    title        TEXT NOT NULL,
    content      TEXT NOT NULL,
    length       INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    crawled_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash);
CREATE TABLE IF NOT EXISTS postings (
    term   TEXT NOT NULL,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tf     INTEGER NOT NULL,
    PRIMARY KEY (term, doc_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_postings_doc ON postings(doc_id);
"""


@dataclass(frozen=True)
class IndexStats:
    documents: int
    terms: int
    avg_length: float


@dataclass(frozen=True)
class Document:
    id: int
    url: str
    title: str
    content: str


class Index:
    """SQLite-backed inverted index: term -> (doc, term frequency)."""

    def __init__(self, path: str | Path = "pysearch.db"):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)

    def add_document(self, url: str, title: str, text: str) -> int | None:
        """Index (or re-index) a page. Returns the doc id, or None if not indexed.

        The check and the replacement happen in one transaction. If the new content is
        unindexable or exactly duplicates another URL's content, the page is not indexed,
        and any previous version stored for this URL is removed so stale text can never
        be returned by a search.

        Only the first MAX_INDEXED_CHARS characters are used, for the postings, the stored
        text and the document length alike, so BM25 length normalisation always describes
        exactly what was indexed and every match can be shown in a snippet.
        """
        text = text[:MAX_INDEXED_CHARS]
        tokens = tokenize(title) * TITLE_WEIGHT + tokenize(text)
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        with self._lock, self._conn:
            duplicate = bool(tokens) and self._conn.execute(
                "SELECT 1 FROM documents WHERE content_hash = ? AND url != ? LIMIT 1",
                (digest, url),
            ).fetchone()
            self._conn.execute("DELETE FROM documents WHERE url = ?", (url,))
            if not tokens or duplicate:
                return None
            cur = self._conn.execute(
                "INSERT INTO documents (url, title, content, length, content_hash, crawled_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (url, title, text, len(tokens), digest, time.time()),
            )
            doc_id = cur.lastrowid
            self._conn.executemany(
                "INSERT INTO postings (term, doc_id, tf) VALUES (?, ?, ?)",
                ((term, doc_id, tf) for term, tf in Counter(tokens).items()),
            )
        return doc_id

    def remove_document(self, url: str) -> bool:
        """Delete a URL and its postings. Returns True if something was removed."""
        with self._lock, self._conn:
            return self._conn.execute("DELETE FROM documents WHERE url = ?", (url,)).rowcount > 0

    def postings(self, term: str) -> list[tuple[int, int, int]]:
        """Return (doc_id, term_frequency, doc_length) for every doc containing term."""
        with self._lock:
            return self._conn.execute(
                "SELECT p.doc_id, p.tf, d.length FROM postings p"
                " JOIN documents d ON d.id = p.doc_id WHERE p.term = ?",
                (term,),
            ).fetchall()

    def get_documents(self, ids: list[int]) -> dict[int, Document]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, url, title, content FROM documents WHERE id IN ({marks})", ids
            ).fetchall()
        return {r[0]: Document(*r) for r in rows}

    def stats(self) -> IndexStats:
        with self._lock:
            n, avg = self._conn.execute(
                "SELECT COUNT(*), COALESCE(AVG(length), 0) FROM documents"
            ).fetchone()
            terms = self._conn.execute("SELECT COUNT(DISTINCT term) FROM postings").fetchone()[0]
        return IndexStats(documents=n, terms=terms, avg_length=float(avg))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Index:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
