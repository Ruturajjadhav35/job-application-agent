"""Shared plumbing for every source: polite HTTP, rate limits and retries."""

import logging
import time
from collections.abc import Callable, Iterator
from typing import Protocol
from urllib.parse import urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from job_agent.config import SearchConfig
from job_agent.schemas import JobPosting

log = logging.getLogger(__name__)

USER_AGENT = "job-agent/0.1 (personal job search tool; contact via GitHub Ruturajjadhav35)"


class JobSource(Protocol):
    name: str

    def fetch(self, search: SearchConfig) -> Iterator[JobPosting]: ...


class SourceConfigError(Exception):
    """A source is missing keys or settings it needs."""


class RequestBudgetExceeded(Exception):
    """The source hit its per-run request limit."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, httpx.TransportError)


class PoliteClient:
    """An httpx client that waits between requests to the same site and retries politely.

    - At least `min_interval` seconds between requests to one host.
    - Retries 429 and 5xx errors up to 3 times, waiting longer each time.
    - Stops after `max_requests` so a bug can't burn through an API quota.
    """

    def __init__(
        self,
        min_interval: float = 3.0,
        max_requests: int = 100,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.min_interval = min_interval
        self.max_requests = max_requests
        self.requests_made = 0
        self._last_request: dict[str, float] = {}
        self._sleep = sleep
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            transport=transport,
            follow_redirects=True,
        )

    def _wait_for_turn(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            gap = time.monotonic() - last
            if gap < self.min_interval:
                self._sleep(self.min_interval - gap)
        self._last_request[host] = time.monotonic()

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def get_json(self, url: str, params: dict | None = None) -> dict:
        if self.requests_made >= self.max_requests:
            raise RequestBudgetExceeded(f"Stopped after {self.max_requests} requests this run.")
        self._wait_for_turn(urlparse(url).netloc)
        self.requests_made += 1
        response = self._client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
