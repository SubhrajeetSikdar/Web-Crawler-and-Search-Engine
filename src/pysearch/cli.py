from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from pysearch.config import CrawlConfig
from pysearch.crawler import Crawler
from pysearch.index import Index
from pysearch.search import SearchEngine


def _emphasise(result) -> str:
    """Bold the matched words when writing to a terminal."""
    if not sys.stdout.isatty():
        return result.snippet
    units = result.snippet.encode("utf-16-le")
    out, pos = [], 0
    for start, end in result.highlights:
        out += [units[pos * 2 : start * 2].decode("utf-16-le"), "\033[1m",
                units[start * 2 : end * 2].decode("utf-16-le"), "\033[0m"]
        pos = end
    out.append(units[pos * 2 :].decode("utf-16-le"))
    return "".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pysearch", description="Web crawler and search engine")
    p.add_argument("--db", default="pysearch.db", help="SQLite index path (default: pysearch.db)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("crawl", help="crawl sites and add them to the index")
    c.add_argument("seeds", nargs="+", help="starting URLs")
    c.add_argument("--max-pages", type=int, default=100)
    c.add_argument("--max-depth", type=int, default=3)
    c.add_argument("--concurrency", type=int, default=10)
    c.add_argument("--delay", type=float, default=1.0, help="seconds between hits on one host")
    c.add_argument("--follow-external", action="store_true", help="follow links to other hosts")
    c.add_argument("--ignore-robots", action="store_true", help="do not obey robots.txt")
    c.add_argument("--retries", type=int, default=2, help="extra attempts on 429/5xx/network errors")
    c.add_argument(
        "--allow-private",
        action="store_true",
        help="DISABLE the SSRF guard (localhost/private IPs); for local testing only",
    )

    s = sub.add_parser("search", help="query the index from the terminal")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)

    sub.add_parser("stats", help="show index statistics")

    e = sub.add_parser("eval", help="measure search quality against labelled queries")
    e.add_argument("judgments", help="JSON file of queries and their relevant URLs")
    e.add_argument("--k", type=int, default=10)
    e.add_argument("--per-query", action="store_true", help="print one line per query")

    sv = sub.add_parser("serve", help="run the search API and web UI")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.command == "serve":
        import uvicorn

        from pysearch.api import create_app

        uvicorn.run(create_app(args.db), host=args.host, port=args.port)
        return 0

    with Index(args.db) as index:
        if args.command == "crawl":
            try:
                config = CrawlConfig(
                    max_pages=args.max_pages,
                    max_depth=args.max_depth,
                    concurrency=args.concurrency,
                    delay=args.delay,
                    retries=args.retries,
                    same_domain=not args.follow_external,
                    respect_robots=not args.ignore_robots,
                    allow_private_hosts=args.allow_private,
                )
            except ValueError as exc:
                parser.error(str(exc))
            stats = asyncio.run(Crawler(config, index).crawl(args.seeds))
            print(
                f"scheduled={stats.scheduled} fetched={stats.fetched} indexed={stats.indexed} "
                f"skipped={stats.skipped} removed={stats.removed} "
                f"robots_blocked={stats.robots_blocked} blocked={stats.blocked} "
                f"retries={stats.retries} errors={stats.errors}"
            )
        elif args.command == "search":
            response = SearchEngine(index).search(args.query, limit=args.limit)
            print(f"{response.total} results in {response.took_ms} ms\n")
            for i, r in enumerate(response.results, 1):
                print(f"{i}. {r.title}  [{r.score}]\n   {r.url}\n   {_emphasise(r)}\n")
        elif args.command == "eval":
            from pysearch.evaluation import evaluate, load_judgments

            try:
                report = evaluate(SearchEngine(index), load_judgments(args.judgments), k=args.k)
            except (ValueError, OSError, KeyError) as exc:
                parser.error(f"cannot evaluate: {exc}")
            if args.per_query:
                for row in report.per_query:
                    print(f"P={row['precision']:.2f} RR={row['rr']:.2f} NDCG={row['ndcg']:.2f}  {row['query']}")
            print(report.summary())
        elif args.command == "stats":
            s = index.stats()
            print(f"documents={s.documents} terms={s.terms} avg_length={s.avg_length:.1f}")
    return 0
