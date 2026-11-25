from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CrawlConfig:
    """Tunable crawler settings. Invalid values raise ValueError immediately."""

    user_agent: str = "PySearchBot/0.2 (+https://github.com/your-username/pysearch)"
    max_pages: int = 100          # hard cap on URLs scheduled in one run
    max_depth: int = 3            # link hops from a seed URL
    concurrency: int = 10         # number of async workers
    delay: float = 1.0            # minimum seconds between requests to one host
    timeout: float = 15.0         # total request timeout in seconds
    max_bytes: int = 2_000_000    # response body size cap
    max_redirects: int = 5        # redirect hops, each one re-validated
    retries: int = 2              # extra attempts on network errors, 429 and 5xx
    retry_backoff: float = 0.5    # seconds, doubled after every failed attempt
    same_domain: bool = True      # only follow links on the seeds' hosts (www. ignored)
    respect_robots: bool = True   # honour robots.txt (Disallow and Crawl-delay)
    allow_private_hosts: bool = False  # SSRF guard off switch; for local testing only

    def __post_init__(self) -> None:
        checks = [
            (bool(self.user_agent.strip()), "user_agent must not be empty"),
            (self.max_pages >= 1, "max_pages must be >= 1"),
            (self.max_depth >= 0, "max_depth must be >= 0"),
            (self.concurrency >= 1, "concurrency must be >= 1"),
            (self.delay >= 0, "delay must be >= 0"),
            (self.timeout > 0, "timeout must be > 0"),
            (self.max_bytes >= 1, "max_bytes must be >= 1"),
            (self.max_redirects >= 0, "max_redirects must be >= 0"),
            (self.retries >= 0, "retries must be >= 0"),
            (self.retry_backoff >= 0, "retry_backoff must be >= 0"),
        ]
        for ok, message in checks:
            if not ok:
                raise ValueError(message)
