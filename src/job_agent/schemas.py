"""The one job schema every source adapter must return."""

import hashlib
import re
from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

_COMPANY_NOISE = re.compile(
    r"\b(pvt|private|ltd|limited|inc|llp|llc|corp|corporation|co|company|technologies"
    r"|technology|solutions|services|careers|india|global|group)\b"
)


def _normalise(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return " ".join(text.split())


def _normalise_company(name: str) -> str:
    # "Athena Health" and "athenahealth", "Danaher Corporation" and "Danaher" match.
    cleaned = _COMPANY_NOISE.sub(" ", _normalise(name)).replace(" ", "")
    return cleaned or _normalise(name).replace(" ", "")


def make_dedup_key(company: str, title: str, city: str | None) -> str:
    """Same company + title + city => same key, whichever site the job came from."""
    from job_agent.text_utils import normalise_city

    raw = "|".join(
        [_normalise_company(company), _normalise(title), _normalise(normalise_city(city) or "")]
    )
    return hashlib.sha256(raw.encode()).hexdigest()


class JobPosting(BaseModel):
    """A job as returned by any source adapter, before it is stored."""

    title: str = Field(min_length=2, max_length=300)
    company: str = Field(min_length=1, max_length=200)
    city: str | None = None
    is_remote: bool = False
    salary_min_inr: int | None = Field(default=None, ge=0)
    salary_max_inr: int | None = Field(default=None, ge=0)
    experience_min_years: int | None = Field(default=None, ge=0)
    experience_max_years: int | None = Field(default=None, ge=0)
    description: str = ""
    apply_url: HttpUrl
    source: str = Field(min_length=1)  # e.g. "adzuna", "greenhouse", "gmail_alert"
    source_job_id: str | None = None
    posted_at: datetime | None = None

    @field_validator("title", "company", "city", "description", mode="before")
    @classmethod
    def strip_text(cls, v):
        return v.strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def check_ranges(self) -> "JobPosting":
        if (
            self.salary_min_inr
            and self.salary_max_inr
            and self.salary_min_inr > self.salary_max_inr
        ):
            raise ValueError("salary_min_inr is higher than salary_max_inr")
        if (
            self.experience_min_years is not None
            and self.experience_max_years is not None
            and self.experience_min_years > self.experience_max_years
        ):
            raise ValueError("experience_min_years is higher than experience_max_years")
        return self

    @property
    def dedup_key(self) -> str:
        return make_dedup_key(self.company, self.title, self.city)
