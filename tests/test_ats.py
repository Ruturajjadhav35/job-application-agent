import html

import httpx
import pytest
from sqlmodel import select

from job_agent.config import Board, CompaniesConfig, SearchConfig
from job_agent.db import change_status, upsert_job
from job_agent.ingest import ingest
from job_agent.lifecycle import Actor, JobStatus
from job_agent.models import Job
from job_agent.schemas import JobPosting
from job_agent.sources.ats import GreenhouseSource, LeverSource
from job_agent.sources.base import PoliteClient

SEARCH = SearchConfig(
    roles=["Machine Learning Engineer", "Data Engineer"],
    locations=["Bengaluru", "Pune"],
    max_experience_years=1,
    must_have_skills=["Python"],
)
COMPANIES = CompaniesConfig(
    greenhouse=[
        Board(slug="acme", name="Acme", india_company=False),
        Board(slug="gone", name="Gone"),
    ],
    lever=[Board(slug="desi", name="Desi", india_company=True)],
    title_keywords=["engineer", "data", "analyst", "ml", "ai"],
)

LONG_DESC = (
    "<p>Build <strong>ML</strong> pipelines in Python &amp; SQL.</p>"
    "<ul><li>0-1 years of experience</li><li>dbt, Snowflake</li></ul>" + "<p>More.</p>" * 40
)


def gh_job(i, title, location, offices=()):
    return {
        "id": i,
        "title": title,
        "updated_at": "2026-10-10T09:00:00-04:00",
        "location": {"name": location},
        "absolute_url": f"https://job-boards.greenhouse.io/acme/jobs/{i}",
        "content": html.escape(LONG_DESC),  # Greenhouse sends HTML escaped
        "offices": [{"name": o, "location": o} for o in offices],
    }


GREENHOUSE = {
    "jobs": [
        gh_job(1, "Machine Learning Engineer", "Bengaluru-VTP, India"),
        gh_job(2, "Data Engineer", "Kuala Lumpur, Malaysia"),
        gh_job(3, "Copywriter", "Bengaluru, India"),  # not technical
        gh_job(4, "Software Engineer II", "Remote, USA"),
        gh_job(5, "Data Analyst", "Hybrid", offices=["Pune, India"]),  # India only via office
    ],
    "meta": {"total": 5},
}


def lever_job(i, title, location, country=None, workplace="on-site"):
    return {
        "id": f"lv-{i}",
        "text": title,
        "categories": {"location": location, "allLocations": [location]},
        "country": country,
        "workplaceType": workplace,
        "descriptionPlain": "We are hiring. " * 20,
        "lists": [{"text": "Requirements", "content": "<li>Python</li><li>0-1 years</li>"}],
        "additionalPlain": "Equal opportunity employer.",
        "hostedUrl": f"https://jobs.lever.co/desi/lv-{i}",
        "applyUrl": f"https://jobs.lever.co/desi/lv-{i}/apply",
        "createdAt": 1760000000000,
        "salaryRange": {
            "currency": "INR",
            "interval": "per-year-salary",
            "min": 1200000,
            "max": 1800000,
        },
    }


def make_client(routes, calls=None):
    def handler(req):
        if calls is not None:
            calls.append(req)
        for prefix, reply in routes.items():
            if str(req.url).startswith(prefix):
                return reply(req) if callable(reply) else reply
        return httpx.Response(404)

    return PoliteClient(
        min_interval=0, sleep=lambda s: None, transport=httpx.MockTransport(handler)
    )


GH_URL = "https://boards-api.greenhouse.io/v1/boards/"
LV_URL = "https://api.lever.co/v0/postings/desi"


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    monkeypatch.setattr(PoliteClient.get_json.retry, "sleep", lambda s: None)


# ---------- Greenhouse ----------


def test_greenhouse_keeps_india_tech_jobs_with_full_text():
    calls = []
    src = GreenhouseSource(
        COMPANIES, make_client({GH_URL + "acme/": httpx.Response(200, json=GREENHOUSE)}, calls)
    )
    jobs = list(src.fetch(SEARCH))
    assert [j.title for j in jobs] == ["Machine Learning Engineer", "Data Analyst"]
    ml = jobs[0]
    assert ml.city == "Bengaluru" and ml.company == "Acme" and ml.source == "greenhouse"
    assert "Python & SQL" in ml.description and "<" not in ml.description  # unescaped, no tags
    assert (ml.experience_min_years, ml.experience_max_years) == (0, 1)
    assert jobs[1].city == "Pune"
    assert calls[0].url.params["content"] == "true"


def test_greenhouse_reports_and_survives_a_missing_board():
    src = GreenhouseSource(
        COMPANIES, make_client({GH_URL + "acme/": httpx.Response(200, json=GREENHOUSE)})
    )
    list(src.fetch(SEARCH))
    acme, gone = src.reports
    assert (acme.total, acme.in_india, acme.kept) == (5, 3, 2)
    assert gone.status.startswith("not found")


# ---------- Lever ----------


def test_lever_pages_and_maps_fields():
    page1 = [lever_job(i, f"Data Engineer {i}", "Bangalore") for i in range(100)]
    page2 = [
        lever_job(100, "AI Engineer", "Remote", workplace="remote"),
        lever_job(101, "Sales Lead", "Bangalore"),
        lever_job(102, "ML Engineer", "Singapore", country="SG"),
    ]
    calls = []

    def reply(req):
        return httpx.Response(200, json=page1 if req.url.params["skip"] == "0" else page2)

    src = LeverSource(COMPANIES, make_client({LV_URL: reply}, calls))
    jobs = list(src.fetch(SEARCH))
    assert len(calls) == 2 and calls[1].url.params["skip"] == "100"
    titles = [j.title for j in jobs]
    assert "Sales Lead" not in titles and "ML Engineer" not in titles
    remote = next(j for j in jobs if j.title == "AI Engineer")
    assert remote.is_remote and remote.city is None  # india_company: plain "Remote" counts
    first = jobs[0]
    assert first.city == "Bengaluru" and first.salary_min_inr == 1200000
    assert "Requirements" in first.description and "- Python" in first.description
    assert str(first.apply_url).endswith("/apply") and first.posted_at.year == 2025


def test_lever_country_code_counts_as_india():
    src = LeverSource(
        CompaniesConfig(lever=[Board(slug="desi", name="Desi")], title_keywords=["engineer"]),
        make_client(
            {
                LV_URL: httpx.Response(
                    200, json=[lever_job(1, "Data Engineer", "Head office", country="IN")]
                )
            }
        ),
    )
    assert [j.title for j in src.fetch(SEARCH)] == ["Data Engineer"]


# ---------- upgrading Adzuna jobs with the full description ----------


def adzuna_copy(session, status=JobStatus.SHORTLISTED, desc="Short summary of the role."):
    p = JobPosting(
        title="Machine Learning Engineer",
        company="Acme",
        city="Bangalore",
        description=desc,
        apply_url="https://www.adzuna.in/details/1",
        source="adzuna",
    )
    job, _ = upsert_job(session, p)
    if status != JobStatus.NEW:
        change_status(session, job, JobStatus.SHORTLISTED, Actor.AGENT)
    job.match_score = 70.0
    session.add(job)
    session.commit()
    return job


def test_board_copy_upgrades_adzuna_job(session):
    job = adzuna_copy(session)
    src = GreenhouseSource(
        COMPANIES, make_client({GH_URL + "acme/": httpx.Response(200, json=GREENHOUSE)})
    )
    stats = ingest(src, SEARCH, session)
    session.refresh(job)
    assert stats.enriched == 1 and stats.new == 1  # the Data Analyst is new
    assert "Python & SQL" in job.description and job.source == "greenhouse"
    assert "greenhouse.io" in job.apply_url
    assert job.match_score is None  # will be scored again
    assert job.status == JobStatus.SHORTLISTED


def test_full_text_can_reveal_a_rule_failure(session):
    job = adzuna_copy(session)
    senior = {
        "jobs": [
            {
                **gh_job(1, "Machine Learning Engineer", "Bengaluru, India"),
                "content": html.escape("<p>Needs 5+ years of Python.</p>" * 30),
            }
        ]
    }
    src = GreenhouseSource(
        COMPANIES, make_client({GH_URL + "acme/": httpx.Response(200, json=senior)})
    )
    stats = ingest(src, SEARCH, session)
    session.refresh(job)
    assert stats.rechecked_out == 1 and job.status == JobStatus.FILTERED_OUT
    assert job.notes.startswith("re-checked with full description: experience")


def test_drafted_jobs_are_never_changed(session):
    job = adzuna_copy(session)
    change_status(session, job, JobStatus.DRAFTED, Actor.AGENT)
    src = GreenhouseSource(
        COMPANIES, make_client({GH_URL + "acme/": httpx.Response(200, json=GREENHOUSE)})
    )
    assert ingest(src, SEARCH, session).enriched == 0
    session.refresh(job)
    assert job.description == "Short summary of the role." and job.source == "adzuna"


def test_second_fetch_adds_nothing(session):
    routes = {GH_URL + "acme/": httpx.Response(200, json=GREENHOUSE)}
    ingest(GreenhouseSource(COMPANIES, make_client(routes)), SEARCH, session)
    stats = ingest(GreenhouseSource(COMPANIES, make_client(routes)), SEARCH, session)
    assert (stats.new, stats.duplicates, stats.enriched) == (0, 2, 0)
    assert len(session.exec(select(Job)).all()) == 2


def test_real_companies_file_is_valid():
    from job_agent.config import load_companies_config

    c = load_companies_config()
    assert c.greenhouse and c.lever
    assert len({b.slug for b in c.greenhouse + c.lever}) == len(c.greenhouse) + len(c.lever)
