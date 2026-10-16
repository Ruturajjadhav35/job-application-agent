from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import select

from job_agent.db import change_status, upsert_job
from job_agent.lifecycle import Actor, JobStatus, TransitionError
from job_agent.models import Draft, JobEvent
from job_agent.resume import MasterResume
from job_agent.review import (
    applied_today,
    check_letter,
    latest_draft,
    move,
    pipeline_counts,
    queue,
    save_letter,
    user_moves,
    waiting_too_long,
)
from job_agent.schemas import JobPosting

RESUME = MasterResume.model_validate(
    {
        "name": "Test Person",
        "summary": "AI graduate with 2 years of experience.",
        "skills": {"Core": ["Python", "SQL", "dbt", "Snowflake"]},
        "experience": [
            {
                "title": "AI & Data Intern",
                "company": "YCX.ai",
                "start": "2025-02",
                "bullets": [
                    "Built ETL pipelines with dbt and Snowflake, improving read speed by 30%.",
                    "Cut query time by 45%.",
                ],
            }
        ],
    }
)

LETTER = """Dear Acme Hiring Team,

Acme's data platform runs on Snowflake and dbt, which I used every day at YCX.ai. There I built ETL pipelines that improved read speed by 30% and cut query time by 45%. I have not yet used Kafka and would be keen to learn it. I would be glad to walk you through those pipelines.

Kind regards,
Test Person
"""  # noqa: E501


def drafted_job(session, tmp_path, title="Data Engineer", score=70.0):
    p = JobPosting(
        title=title, company="Acme", city="Pune", apply_url="https://acme.com/j", source="adzuna"
    )
    job, _ = upsert_job(session, p)
    change_status(session, job, JobStatus.SHORTLISTED, Actor.AGENT)
    change_status(session, job, JobStatus.DRAFTED, Actor.AGENT)
    job.match_score = score
    folder = tmp_path / f"draft-{job.id}"
    folder.mkdir()
    (folder / "cover_letter.txt").write_text(LETTER)
    draft = Draft(
        job_id=job.id,
        model="fake",
        passed=True,
        folder=str(folder),
        plan={"summary": "AI graduate with 2 years of experience.", "fit": {"gaps": ["Kafka"]}},
        resume_tex="\\documentclass{article}",
        cover_letter=LETTER,
    )
    session.add_all([job, draft])
    session.commit()
    return job, draft


def test_queue_orders_by_score_and_attaches_latest_passed_draft(session, tmp_path):
    low, _ = drafted_job(session, tmp_path, "Data Analyst", 40)
    high, d = drafted_job(session, tmp_path, "ML Engineer", 80)
    session.add(Draft(job_id=high.id, model="fake", passed=False, plan={}))  # failed retry
    session.commit()
    items = queue(session, JobStatus.DRAFTED)
    assert [i.job.id for i in items] == [high.id, low.id]
    assert items[0].draft.id == d.id
    assert latest_draft(session, high.id).passed


def test_full_user_journey_is_logged_as_user(session, tmp_path):
    job, _ = drafted_job(session, tmp_path)
    move(session, job, JobStatus.APPROVED)
    move(session, job, JobStatus.APPLIED)
    move(session, job, JobStatus.INTERVIEW, "phone screen booked")
    move(session, job, JobStatus.OFFER)
    user_events = session.exec(select(JobEvent).where(JobEvent.actor == Actor.USER)).all()
    assert [e.to_status for e in user_events] == [
        JobStatus.APPROVED,
        JobStatus.APPLIED,
        JobStatus.INTERVIEW,
        JobStatus.OFFER,
    ]
    assert user_events[2].reason == "you: phone screen booked"
    assert job.notes == "phone screen booked"


def test_can_withdraw_approval_but_cannot_skip_steps(session, tmp_path):
    job, _ = drafted_job(session, tmp_path)
    with pytest.raises(TransitionError):
        move(session, job, JobStatus.APPLIED)  # must approve first
    move(session, job, JobStatus.APPROVED)
    move(session, job, JobStatus.REJECTED, "role closed")
    assert job.status == JobStatus.REJECTED


def test_user_moves_match_lifecycle():
    assert user_moves(JobStatus.DRAFTED) == [JobStatus.APPROVED, JobStatus.REJECTED]
    assert set(user_moves(JobStatus.APPLIED)) == {JobStatus.INTERVIEW, JobStatus.NO_RESPONSE}
    assert user_moves(JobStatus.SHORTLISTED) == []  # the agent's job, not yours


def test_counts_and_applied_today(session, tmp_path):
    job, _ = drafted_job(session, tmp_path)
    move(session, job, JobStatus.APPROVED)
    move(session, job, JobStatus.APPLIED)
    counts = pipeline_counts(session)
    assert counts[JobStatus.APPLIED] == 1 and counts[JobStatus.DRAFTED] == 0
    assert applied_today(session) == 1


def test_waiting_too_long(session, tmp_path):
    job, _ = drafted_job(session, tmp_path)
    move(session, job, JobStatus.APPROVED)
    move(session, job, JobStatus.APPLIED)
    assert waiting_too_long(session) == []
    job.status_updated_at = datetime.now(UTC) - timedelta(days=22)
    session.add(job)
    session.commit()
    assert [j.id for j in waiting_too_long(session)] == [job.id]


def test_save_letter_keeps_agent_original(session, tmp_path):
    _, draft = drafted_job(session, tmp_path)
    folder = Path(draft.folder)
    save_letter(session, draft, LETTER.replace("glad", "happy"))
    save_letter(session, draft, LETTER.replace("glad", "pleased"))
    assert "glad" in (folder / "cover_letter.original.txt").read_text()  # first version kept
    assert "pleased" in (folder / "cover_letter.txt").read_text()
    assert "pleased" in session.get(Draft, draft.id).cover_letter


def test_check_letter_flags_invented_numbers_in_your_edits(session, tmp_path):
    job, draft = drafted_job(session, tmp_path)
    assert check_letter(draft, LETTER, RESUME, job).passed
    report = check_letter(draft, LETTER.replace("by 30%", "by 70%"), RESUME, job)
    assert any("'70'" in p for p in report.problems)
