"""Company job boards: Greenhouse and Lever.

These are public APIs that the boards publish so job listings can be shown elsewhere,
so reading them is what they're for. Unlike Adzuna they return the FULL job
description. One request per board (Lever pages in 100s), paced by PoliteClient.

Docs:
  Greenhouse: https://developers.greenhouse.io/job-board.html
  Lever:      https://github.com/lever/postings-api
"""

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from job_agent.config import Board, CompaniesConfig, SearchConfig, load_companies_config
from job_agent.schemas import JobPosting
from job_agent.sources.base import PoliteClient, RequestBudgetExceeded
from job_agent.text_utils import extract_experience, html_to_text, india_location

log = logging.getLogger(__name__)


@dataclass
class BoardReport:
    source: str
    slug: str
    name: str
    status: str = "ok"
    total: int = 0  # every job on the board
    in_india: int = 0  # of those, in India (or remote for India)
    kept: int = 0  # of those, technical titles that became postings

    def __str__(self) -> str:
        if self.status != "ok":
            return f"{self.source:<10} {self.name:<12} {self.status}"
        return (
            f"{self.source:<10} {self.name:<12} {self.total:>4} jobs, {self.in_india:>3} in India, "
            f"{self.kept:>3} technical kept"
        )


class _BoardSource:
    name = "board"

    def __init__(
        self, companies: CompaniesConfig | None = None, client: PoliteClient | None = None
    ):
        self.companies = companies or load_companies_config()
        self.client = client or PoliteClient(min_interval=2.0, max_requests=60)
        self.reports: list[BoardReport] = []
        self.skipped = 0

    # each subclass: which boards, and how to read one
    def boards(self) -> list[Board]:
        raise NotImplementedError

    def board_jobs(self, board: Board) -> list[dict]:
        raise NotImplementedError

    def to_posting(self, raw: dict, board: Board) -> tuple[JobPosting | None, bool]:
        """Returns (posting or None, in_india)."""
        raise NotImplementedError

    def _title_re(self, search: SearchConfig) -> re.Pattern:
        words = set(w.lower() for w in self.companies.title_keywords)
        for role in search.roles:
            words.update(w.lower() for w in role.split() if len(w) > 2)
        return re.compile(
            r"\b(" + "|".join(sorted(map(re.escape, words), key=len, reverse=True)) + r")\b", re.I
        )

    def fetch(self, search: SearchConfig) -> Iterator[JobPosting]:
        title_ok = self._title_re(search)
        for board in self.boards():
            report = BoardReport(self.name, board.slug, board.name)
            self.reports.append(report)
            try:
                raws = self.board_jobs(board)
            except RequestBudgetExceeded as e:
                report.status = f"stopped: {e}"
                log.warning("%s: %s", self.name, e)
                return
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                report.status = (
                    "not found: check the slug in config/companies.yaml"
                    if code == 404
                    else f"error HTTP {code}"
                )
                log.error("%s %s: %s", self.name, board.slug, report.status)
                continue
            except httpx.TransportError as e:
                report.status = f"network error: {type(e).__name__}"
                continue
            report.total = len(raws)
            for raw in raws:
                posting, in_india = self.to_posting(raw, board)
                if not in_india:
                    continue
                report.in_india += 1
                if posting is None or not title_ok.search(posting.title):
                    continue
                report.kept += 1
                yield posting
            log.info("%s", report)


def _posting(**kw) -> JobPosting | None:
    try:
        return JobPosting(**kw)
    except ValidationError as e:
        log.debug("skipped invalid job: %s", e.errors()[0]["msg"])
        return None


def _iso(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


class GreenhouseSource(_BoardSource):
    name = "greenhouse"
    URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"

    def boards(self) -> list[Board]:
        return self.companies.greenhouse

    def board_jobs(self, board: Board) -> list[dict]:
        data = self.client.get_json(self.URL.format(slug=board.slug), params={"content": "true"})
        return data.get("jobs", [])

    def to_posting(self, raw: dict, board: Board) -> tuple[JobPosting | None, bool]:
        places = [(raw.get("location") or {}).get("name") or ""]
        for office in raw.get("offices") or []:
            places += [office.get("name") or "", office.get("location") or ""]
        where = " | ".join(p for p in places if p)
        in_india, city, remote = india_location(where, assume_india=board.india_company)
        if not in_india:
            return None, False
        description = html_to_text(raw.get("content") or "")
        title = raw.get("title") or ""
        exp_min, exp_max = extract_experience(f"{title}\n{description}")
        posting = _posting(
            title=title,
            company=board.name,
            city=city,
            is_remote=remote,
            experience_min_years=exp_min,
            experience_max_years=exp_max,
            description=description,
            apply_url=raw.get("absolute_url") or "",
            source=self.name,
            source_job_id=str(raw.get("id") or "") or None,
            posted_at=_iso(raw.get("first_published") or raw.get("updated_at")),
        )
        if posting is None:
            self.skipped += 1
        return posting, True


class LeverSource(_BoardSource):
    name = "lever"
    URL = "https://api.lever.co/v0/postings/{slug}"
    PAGE = 100

    def boards(self) -> list[Board]:
        return self.companies.lever

    def board_jobs(self, board: Board) -> list[dict]:
        jobs: list[dict] = []
        while True:
            page = self.client.get_json(
                self.URL.format(slug=board.slug),
                params={"mode": "json", "skip": len(jobs), "limit": self.PAGE},
            )
            if not isinstance(page, list):
                break
            jobs += page
            if len(page) < self.PAGE:
                break
        return jobs

    def to_posting(self, raw: dict, board: Board) -> tuple[JobPosting | None, bool]:
        cats = raw.get("categories") or {}
        places = [cats.get("location") or ""] + list(cats.get("allLocations") or [])
        where = " | ".join(dict.fromkeys(p for p in places if p))
        in_india, city, remote = india_location(
            where, country=raw.get("country"), assume_india=board.india_company
        )
        if not in_india:
            return None, False
        remote = remote or raw.get("workplaceType") == "remote"

        parts = [raw.get("descriptionPlain") or ""]
        for section in raw.get("lists") or []:
            parts.append(f"{section.get('text', '')}\n{html_to_text(section.get('content', ''))}")
        parts.append(raw.get("additionalPlain") or "")
        description = "\n\n".join(p.strip() for p in parts if p and p.strip())

        sal_min = sal_max = None
        salary = raw.get("salaryRange") or {}
        if salary.get("currency") == "INR" and "year" in str(salary.get("interval", "")):
            sal_min, sal_max = salary.get("min"), salary.get("max")

        created = raw.get("createdAt")
        posted = (
            datetime.fromtimestamp(created / 1000, tz=UTC)
            if isinstance(created, int | float)
            else _iso(created)
        )
        title = raw.get("text") or ""
        exp_min, exp_max = extract_experience(f"{title}\n{description}")
        posting = _posting(
            title=title,
            company=board.name,
            city=city,
            is_remote=remote,
            salary_min_inr=sal_min,
            salary_max_inr=sal_max,
            experience_min_years=exp_min,
            experience_max_years=exp_max,
            description=description,
            apply_url=raw.get("applyUrl") or raw.get("hostedUrl") or "",
            source=self.name,
            source_job_id=raw.get("id"),
            posted_at=posted,
        )
        if posting is None:
            self.skipped += 1
        return posting, True
