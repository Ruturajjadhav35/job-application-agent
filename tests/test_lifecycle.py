import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlmodel import select

from job_agent.db import change_status, upsert_job
from job_agent.lifecycle import Actor, JobStatus, TransitionError
from job_agent.models import JobEvent
from job_agent.schemas import JobPosting


def test_new_job_starts_as_new_and_is_logged(session, posting_data):
    job, created = upsert_job(session, JobPosting(**posting_data))
    assert created and job.status == JobStatus.NEW
    events = session.exec(select(JobEvent).where(JobEvent.job_id == job.id)).all()
    assert [e.to_status for e in events] == [JobStatus.NEW]


def test_duplicate_does_not_reset_status(session, posting_data):
    job, _ = upsert_job(session, JobPosting(**posting_data))
    change_status(session, job, JobStatus.SHORTLISTED, Actor.AGENT)
    again, created = upsert_job(session, JobPosting(**{**posting_data, "source": "adzuna"}))
    assert not created and again.id == job.id and again.status == JobStatus.SHORTLISTED


def test_full_happy_path(session, posting_data):
    job, _ = upsert_job(session, JobPosting(**posting_data))
    change_status(session, job, JobStatus.SHORTLISTED, Actor.AGENT)
    change_status(session, job, JobStatus.DRAFTED, Actor.AGENT)
    change_status(session, job, JobStatus.APPROVED, Actor.USER)
    change_status(session, job, JobStatus.APPLIED, Actor.USER)
    change_status(session, job, JobStatus.INTERVIEW, Actor.USER)
    change_status(session, job, JobStatus.OFFER, Actor.USER)
    assert job.status == JobStatus.OFFER
    assert len(session.exec(select(JobEvent)).all()) == 7


def test_agent_cannot_approve(session, posting_data):
    job, _ = upsert_job(session, JobPosting(**posting_data))
    change_status(session, job, JobStatus.SHORTLISTED, Actor.AGENT)
    change_status(session, job, JobStatus.DRAFTED, Actor.AGENT)
    with pytest.raises(TransitionError, match="Only the user"):
        change_status(session, job, JobStatus.APPROVED, Actor.AGENT)


def test_agent_cannot_skip_to_applied(session, posting_data):
    job, _ = upsert_job(session, JobPosting(**posting_data))
    with pytest.raises(TransitionError):
        change_status(session, job, JobStatus.APPLIED, Actor.AGENT)


def test_filtered_out_can_only_go_back_to_shortlisted(session, posting_data):
    job, _ = upsert_job(session, JobPosting(**posting_data))
    change_status(session, job, JobStatus.FILTERED_OUT, Actor.AGENT, reason="scam: fee requested")
    with pytest.raises(TransitionError):
        change_status(session, job, JobStatus.DRAFTED, Actor.AGENT)
    change_status(session, job, JobStatus.SHORTLISTED, Actor.AGENT, reason="re-screened")
    assert job.status == JobStatus.SHORTLISTED


def test_database_rejects_unknown_status(session, posting_data):
    job, _ = upsert_job(session, JobPosting(**posting_data))
    with pytest.raises((IntegrityError, StatementError)):
        session.execute(text("UPDATE jobs SET status='hired' WHERE id=:id"), {"id": job.id})
        session.commit()
