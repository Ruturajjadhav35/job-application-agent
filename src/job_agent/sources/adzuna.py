"""Adzuna job search API (India). Docs: https://developer.adzuna.com/

Note: Adzuna returns a shortened description (a few hundred characters), not
the full job description. That's enough for filtering and first-pass matching.
"""

import logging
from collections.abc import Iterator
from datetime import datetime

import httpx
from pydantic import ValidationError

from job_agent.config import SearchConfig, get_settings
from job_agent.schemas import JobPosting
from job_agent.sources.base import (
    PoliteClient,
    RequestBudgetExceeded,
    SourceConfigError,
)
from job_agent.text_utils import city_from_area, extract_experience, looks_remote

log = logging.getLogger(__name__)

BASE_URL = "https://api.adzuna.com/v1/api/jobs/in/search/{page}"


class AdzunaSource:
    name = "adzuna"

    def __init__(
        self,
        client: PoliteClient | None = None,
        app_id: str | None = None,
        app_key: str | None = None,
        max_days_old: int = 7,
        results_per_page: int = 50,
        max_pages: int = 1,
        max_queries: int | None = None,
    ):
        settings = get_settings()
        self.app_id = app_id or settings.adzuna_app_id
        key = app_key or (
            settings.adzuna_app_key.get_secret_value() if settings.adzuna_app_key else None
        )
        if not self.app_id or not key:
            raise SourceConfigError(
                "Adzuna keys are missing. Add ADZUNA_APP_ID and ADZUNA_APP_KEY to your .env file."
            )
        self._app_key = key
        self.client = client or PoliteClient(min_interval=3.0, max_requests=100)
        self.max_days_old = max_days_old
        self.results_per_page = results_per_page
        self.max_pages = max_pages
        self.max_queries = max_queries
        self.skipped = 0

    def build_queries(self, search: SearchConfig) -> list[tuple[str, str | None]]:
        """Every role in every city, plus each role as a remote search."""
        queries: list[tuple[str, str | None]] = [
            (role, city) for role in search.roles for city in search.locations
        ]
        if search.include_remote:
            queries += [(f"{role} remote", None) for role in search.roles]
        return queries[: self.max_queries] if self.max_queries else queries

    def fetch(self, search: SearchConfig) -> Iterator[JobPosting]:
        for what, where in self.build_queries(search):
            for page in range(1, self.max_pages + 1):
                params = {
                    "app_id": self.app_id,
                    "app_key": self._app_key,
                    "what": what,
                    "results_per_page": self.results_per_page,
                    "max_days_old": self.max_days_old,
                    "sort_by": "date",
                    "content-type": "application/json",
                }
                if where:
                    params["where"] = where
                if search.min_salary_inr:
                    params["salary_min"] = search.min_salary_inr

                label = f"'{what}'" + (f" in {where}" if where else "")
                try:
                    data = self.client.get_json(BASE_URL.format(page=page), params=params)
                except RequestBudgetExceeded as e:
                    log.warning("Adzuna: %s Remaining searches skipped.", e)
                    return
                except httpx.HTTPStatusError as e:
                    # Log the status only. The URL contains your API key.
                    log.error("Adzuna: %s failed with HTTP %s", label, e.response.status_code)
                    if e.response.status_code in (401, 403):
                        raise SourceConfigError(
                            "Adzuna rejected your keys. Check ADZUNA_APP_ID and ADZUNA_APP_KEY."
                        ) from None
                    break
                except httpx.TransportError as e:
                    log.error("Adzuna: %s network error: %s", label, type(e).__name__)
                    break

                results = data.get("results", [])
                log.info("Adzuna: %s page %d -> %d results", label, page, len(results))
                for raw in results:
                    posting = self.to_posting(raw)
                    if posting:
                        yield posting
                if len(results) < self.results_per_page:
                    break  # no more pages

    def to_posting(self, raw: dict) -> JobPosting | None:
        """Convert one Adzuna result into a JobPosting. Returns None if it's unusable."""
        company = (raw.get("company") or {}).get("display_name")
        if not company or not raw.get("title") or not raw.get("redirect_url"):
            self.skipped += 1
            return None

        description = raw.get("description") or ""
        area = (raw.get("location") or {}).get("area") or []
        city = city_from_area(area)
        exp_min, exp_max = extract_experience(f"{raw['title']} {description}")

        # Adzuna fills in estimated salaries when the employer didn't give one.
        # Estimates aren't real offers, so keep only salaries the employer stated.
        predicted = str(raw.get("salary_is_predicted", "0")) == "1"
        sal_min = None if predicted else _to_int(raw.get("salary_min"))
        sal_max = None if predicted else _to_int(raw.get("salary_max"))

        try:
            return JobPosting(
                title=raw["title"],
                company=company,
                city=city,
                is_remote=looks_remote(raw["title"], description),
                salary_min_inr=sal_min,
                salary_max_inr=sal_max,
                experience_min_years=exp_min,
                experience_max_years=exp_max,
                description=description,
                apply_url=raw["redirect_url"],
                source=self.name,
                source_job_id=str(raw.get("id")) if raw.get("id") else None,
                posted_at=_to_datetime(raw.get("created")),
            )
        except ValidationError as e:
            self.skipped += 1
            log.debug("Adzuna: skipped invalid job %s: %s", raw.get("id"), e.errors()[0]["msg"])
            return None


def _to_int(value) -> int | None:
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
