import pytest

from job_agent.config import SearchConfig
from job_agent.db import upsert_job
from job_agent.filters import has_skill, screen
from job_agent.lifecycle import JobStatus
from job_agent.models import Job
from job_agent.schemas import JobPosting
from job_agent.screening import screen_jobs


@pytest.fixture
def search():
    return SearchConfig(
        roles=["ML Engineer"],
        locations=["Bengaluru", "Pune", "Gurgaon"],
        include_remote=True,
        max_experience_years=1,
        must_have_skills=["Python", "SQL"],
        deal_breakers=["unpaid", "registration fee"],
    )


def job(**kw) -> Job:
    base = dict(
        dedup_key="x",
        title="Machine Learning Engineer",
        company="Acme",
        city="Bengaluru",
        description="Python and SQL, 0-1 years.",
        apply_url="https://acme.com/j/1",
        source="greenhouse",
    )
    return Job(**{**base, **kw})


def test_good_job_is_shortlisted(search):
    r = screen(job(), search)
    assert r.shortlist and r.summary() == "passed checks"


@pytest.mark.parametrize(
    "title",
    [
        "Senior ML Engineer",
        "Sr. Data Scientist",
        "Lead AI Engineer",
        "Principal Engineer",
        "Staff Engineer - ML",
        "Director AI",
        "Engineering Manager",
        "AI Systems Design Architect",
        "Machine Learning Engineer II",
        "SDE-2",
        "AI & Agentic Systems Engineering, AVP",
    ],
)
def test_senior_titles_filtered(search, title):
    r = screen(job(title=title), search)
    assert not r.shortlist and r.reasons[0].startswith("seniority")


@pytest.mark.parametrize(
    "title",
    [
        "Member of Technical Staff - ML",
        "Applied Scientist I",
        "MTS-1 Machine Learning Engineer",
        "Software Development Engineer 1-2",
        "GenAI Developer",
    ],
)
def test_junior_titles_kept(search, title):
    assert screen(job(title=title), search).shortlist


def test_scam_fee(search):
    r = screen(
        job(description="Python, SQL. Pay a refundable security deposit of Rs 2000."), search
    )
    assert not r.shortlist and "asks for a fee or deposit" in r.scam_flags


def test_scam_whatsapp_and_gmail(search):
    r = screen(
        job(description="Python SQL. Apply on WhatsApp 98xxxx, mail hr.jobs@gmail.com"), search
    )
    assert "contact only via WhatsApp/Telegram" in r.scam_flags
    assert "uses a free email address" in r.scam_flags


def test_task_scheme(search):
    r = screen(job(description="Python SQL. Earn 3000 per day! Just like videos."), search)
    assert "task-based earning scheme" in r.scam_flags


def test_deal_breaker(search):
    r = screen(job(description="Python SQL. This is an unpaid internship."), search)
    assert any(x.startswith("deal-breaker") for x in r.reasons)


def test_experience_too_high(search):
    r = screen(job(experience_min_years=3, experience_max_years=5), search)
    assert r.reasons == ["experience: asks for 3+ years"]


def test_unknown_experience_kept(search):
    assert screen(job(experience_min_years=None), search).shortlist


def test_location(search):
    assert not screen(job(city="Kolkata"), search).shortlist
    assert screen(job(city="Gurugram"), search).shortlist  # alias of Gurgaon
    assert screen(job(city="Kolkata", is_remote=True), search).shortlist
    assert screen(job(city=None), search).shortlist  # unknown city kept


def test_off_target(search):
    r = screen(job(title="Technical Recruiter"), search)
    assert r.reasons == ["off-target role: recruiter"]


def test_skills_full_description_filters(search):
    r = screen(job(description="Python only, 0-1 years."), search)
    assert r.reasons == ["missing skills: SQL"]


def test_skills_short_description_only_noted(search):
    r = screen(job(source="adzuna", description="Python only."), search)
    assert r.shortlist and r.unconfirmed_skills == ["SQL"]
    assert "couldn't confirm SQL" in r.summary()


def test_skill_synonyms():
    assert has_skill("Experience with PostgreSQL", "SQL")
    assert has_skill("PySpark pipelines", "Python")
    assert not has_skill("NoSQL databases", "SQL")


def _add(session, title, city="Bengaluru", desc="Python SQL"):
    p = JobPosting(
        title=title,
        company="Acme",
        city=city,
        description=desc,
        apply_url="https://acme.com/j",
        source="greenhouse",
    )
    return upsert_job(session, p)[0]


def test_screen_jobs_updates_status_and_logs(session, search):
    good = _add(session, "ML Engineer")
    bad = _add(session, "Senior ML Engineer")
    stats = screen_jobs(session, search)
    assert (stats.shortlisted, stats.filtered_out) == (1, 1)
    assert good.status == JobStatus.SHORTLISTED
    assert bad.status == JobStatus.FILTERED_OUT and "seniority" in bad.notes


def test_dry_run_changes_nothing(session, search):
    j = _add(session, "Senior ML Engineer")
    screen_jobs(session, search, dry_run=True)
    assert j.status == JobStatus.NEW


def test_rescreen_after_config_change(session, search):
    j = _add(session, "ML Engineer", city="Kolkata")
    screen_jobs(session, search)
    assert j.status == JobStatus.FILTERED_OUT
    search.locations.append("Kolkata")
    stats = screen_jobs(session, search, rescreen=True)
    assert j.status == JobStatus.SHORTLISTED and stats.shortlisted == 1


def test_more_skill_synonyms():
    assert has_skill("Experience with LLM apps", "LLMs")
    assert has_skill("Strong ETL background", "ETL/ELT Pipelines")
    assert has_skill("Uses sklearn daily", "Scikit-learn")
    assert has_skill("Build RESTful services", "REST APIs")
