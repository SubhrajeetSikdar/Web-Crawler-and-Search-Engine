from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser

from pysearch.urls import normalize_url

_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "head"})
_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5",
     "h6", "section", "article", "header", "footer", "pre", "blockquote", "table"}
)


@dataclass
class ParsedPage:
    title: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)
    noindex: bool = False
    nofollow: bool = False


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.hrefs: list[str] = []
        self.base: str | None = None
        self.noindex = False
        self.nofollow = False
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "base" and a.get("href") and self.base is None:
            self.base = a["href"]
        elif tag == "meta" and a.get("name", "").lower() == "robots":
            directives = {d.strip() for d in a.get("content", "").lower().split(",")}
            self.noindex |= "noindex" in directives or "none" in directives
            self.nofollow |= "nofollow" in directives or "none" in directives
        elif tag == "a" and a.get("href") and "nofollow" not in a.get("rel", "").lower():
            self.hrefs.append(a["href"])
        if tag in _SKIP_TAGS and tag != "head":
            self._skip += 1
        if tag in _BLOCK_TAGS:
            self.text_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIP_TAGS and tag != "head" and self._skip:
            self._skip -= 1
        if tag in _BLOCK_TAGS:
            self.text_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        elif not self._skip:
            self.text_parts.append(data)


def _collapse(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


def parse_html(html: str, base_url: str) -> ParsedPage:
    """Extract title, visible text, outgoing links and robots meta directives."""
    extractor = _Extractor()
    try:
        extractor.feed(html)
        extractor.close()
    except Exception:  # malformed markup: keep whatever was parsed so far
        pass
    base = base_url
    if extractor.base:
        base = normalize_url(extractor.base, base_url) or base_url
    seen: set[str] = set()
    links: list[str] = []
    for href in extractor.hrefs:
        link = normalize_url(href, base)
        if link and link not in seen:
            seen.add(link)
            links.append(link)
    return ParsedPage(
        title=_collapse(extractor.title_parts),
        text=_collapse(extractor.text_parts),
        links=links,
        noindex=extractor.noindex,
        nofollow=extractor.nofollow,
    )
