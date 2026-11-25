from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

STOPWORDS = frozenset(
    """a an and are as at be but by for from has have he her his i if in into is it its
    me my not of on or our she so than that the their them then there these they this to
    was we were what when where which who will with you your""".split()
)


def _undouble(word: str) -> str:
    if len(word) >= 2 and word[-1] == word[-2] and word[-1] not in "lsz":
        return word[:-1]
    return word


def stem(word: str) -> str:
    """A deliberately light suffix stemmer (plurals, -ing, -ed).

    Applied identically at index and query time, so exact linguistic accuracy
    matters less than consistency. Swap in a Porter/Snowball stemmer if needed.
    """
    if len(word) <= 3 or word.isdigit():
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("sses") or word.endswith(("xes", "ches", "shes")):
        return word[:-2]
    if word.endswith("ing") and len(word) - 3 >= 3:
        return _undouble(word[:-3])
    if word.endswith("ed") and len(word) - 2 >= 3:
        return _undouble(word[:-2])
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def words(text: str) -> list[str]:
    """Lowercased raw words, no stopword removal or stemming."""
    return _TOKEN_RE.findall(text.lower())


def tokenize(text: str) -> list[str]:
    """Lowercase, split, drop stopwords, stem."""
    return [stem(w) for w in words(text) if w not in STOPWORDS]
