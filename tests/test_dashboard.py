"""Drive the real dashboard with Streamlit's test runner: click buttons, check the database."""

from pathlib import Path

import pytest
import streamlit as st
from sqlmodel import Session, SQLModel, create_engine, select
from streamlit.testing.v1 import AppTest

import job_agent.models  # noqa: F401
from job_agent.db import change_status, upsert_job
from job_agent.lifecycle import Actor, JobStatus
from job_agent.models import Draft, Job
from job_agent.schemas import JobPosting

APP = str(Path(__file__).resolve().parents[1] / "src" / "job_agent" / "dashboard.py")
LETTER = "Dear Acme Hiring Team,\n\nBody of the letter.\n\nKind regards,\nTest\n"


@pytest.fixture
def db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'jobs.db'}"
    monkeypatch.setenv("JOB_AGENT_DB_URL", url)
    st.cache_resource.clear()  # the dashboard caches its engine
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    return engine, tmp_path


def add_drafted(engine, tmp_path, title, score):
    with Session(engine) as s:
        p = JobPosting(
            title=title,
            company="Acme Private Limited",
            city="Pune",
            apply_url="https://acme.com/j",
            source="adzuna",
        )
        job, _ = upsert_job(s, p)
        change_status(s, job, JobStatus.SHORTLISTED, Actor.AGENT)
        change_status(s, job, JobStatus.DRAFTED, Actor.AGENT)
        job.match_score = score
        folder = tmp_path / f"d{job.id}"
        folder.mkdir()
        s.add(
            Draft(
                job_id=job.id,
                model="fake",
                passed=True,
                folder=str(folder),
                plan={"fit": {"strengths": ["dbt"], "gaps": ["Kafka"]}},
                resume_tex="\\documentclass{article}",
                cover_letter=LETTER,
            )
        )
        s.add(job)
        s.commit()
        return job.id


def status_of(engine, job_id):
    with Session(engine) as s:
        return s.get(Job, job_id).status


def run(at):
    at.run(timeout=30)
    assert not at.exception, at.exception
    return at


def test_empty_dashboard_renders(db):
    at = run(AppTest.from_file(APP))
    assert "Nothing to review" in " ".join(i.value for i in at.info)


def test_review_shows_best_match_first_with_short_name(db):
    engine, tmp = db
    add_drafted(engine, tmp, "Data Analyst", 40)
    add_drafted(engine, tmp, "ML Engineer", 80)
    at = run(AppTest.from_file(APP))
    assert (
        at.selectbox(key="review_pick")
        .format_func(at.selectbox(key="review_pick").value)
        .startswith("80 · ML Engineer · Acme")
    )
    assert any("ML Engineer at Acme" in h.value for h in at.subheader)


def test_approve_then_submit_then_interview(db):
    engine, tmp = db
    jid = add_drafted(engine, tmp, "ML Engineer", 80)
    at = run(AppTest.from_file(APP))
    at.button(key=f"approve_{jid}").click()
    run(at)
    assert status_of(engine, jid) == JobStatus.APPROVED

    at.button(key=f"applied_{jid}").click()
    run(at)
    assert status_of(engine, jid) == JobStatus.APPLIED

    at.button(key=f"interview_{jid}").click()
    run(at)
    assert status_of(engine, jid) == JobStatus.INTERVIEW


def test_reject_with_note(db):
    engine, tmp = db
    jid = add_drafted(engine, tmp, "ML Engineer", 80)
    at = run(AppTest.from_file(APP))
    at.text_input(key=f"note_{jid}").input("needs 5 years really")
    at.button(key=f"reject_{jid}").click()
    run(at)
    assert status_of(engine, jid) == JobStatus.REJECTED
    with Session(engine) as s:
        assert s.get(Job, jid).notes == "needs 5 years really"


def test_editing_letter_blocks_approve_until_saved(db):
    engine, tmp = db
    jid = add_drafted(engine, tmp, "ML Engineer", 80)
    at = run(AppTest.from_file(APP))
    with Session(engine) as s:
        draft_id = s.exec(select(Draft).where(Draft.job_id == jid)).one().id

    at.text_area(key=f"letter_{draft_id}").input(LETTER.replace("Body", "My own body"))
    run(at)
    assert at.button(key=f"approve_{jid}").disabled

    at.button(key=f"save_{draft_id}").click()
    run(at)
    with Session(engine) as s:
        assert "My own body" in s.get(Draft, draft_id).cover_letter
    assert (tmp / f"d{jid}" / "cover_letter.original.txt").exists()
    assert not at.button(key=f"approve_{jid}").disabled
