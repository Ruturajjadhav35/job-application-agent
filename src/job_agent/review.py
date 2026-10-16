"""Step 6: everything the review dashboard does, as plain functions.

The dashboard (dashboard.py) is only buttons and layout on top of these, so the
rules here are tested without a browser. Every status change goes through
change_status() as the USER, so the lifecycle and audit log stay intact.

The agent never submits anything. "Mark as applied" records that YOU submitted it.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import func
from sqlmodel import Session, select

from job_agent.db import change_status
from job_agent.lifecycle import ALLOWED_TRANSITIONS, Actor, JobStatus
from job_agent.models import Draft, Job, JobEvent
from job_agent.resume import MasterResume
from job_agent.truthfulness import TruthReport, check_draft

NO_RESPONSE_AFTER_DAYS = 21

# Order the pipeline is shown in.
PIPELINE = [
    JobStatus.SHORTLISTED,
    JobStatus.DRAFTED,
    JobStatus.APPROVED,
    JobStatus.APPLIED,
    JobStatus.INTERVIEW,
    JobStatus.OFFER,
    JobStatus.REJECTED,
    JobStatus.NO_RESPONSE,
    JobStatus.CLOSED,
]


@dataclass
class ReviewItem:
    job: Job
    draft: Draft | None


def _aware(dt: datetime) -> datetime:
    # SQLite gives back naive datetimes; everything here is stored in UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def latest_draft(session: Session, job_id: int, passed_only: bool = True) -> Draft | None:
    query = select(Draft).where(Draft.job_id == job_id)
    if passed_only:
        query = query.where(Draft.passed)
    return session.exec(query.order_by(Draft.created_at.desc(), Draft.id.desc())).first()


def queue(session: Session, status: JobStatus) -> list[ReviewItem]:
    """Jobs in one status, best match first, each with its latest passed draft."""
    jobs = session.exec(
        select(Job)
        .where(Job.status == status)
        .order_by(Job.match_score.desc().nulls_last(), Job.id)
    ).all()
    return [ReviewItem(j, latest_draft(session, j.id)) for j in jobs]


def pipeline_counts(session: Session) -> dict[JobStatus, int]:
    rows = session.exec(select(Job.status, func.count()).group_by(Job.status)).all()
    found = {JobStatus(s): n for s, n in rows}
    return {s: found.get(s, 0) for s in PIPELINE}


def user_moves(status: JobStatus) -> list[JobStatus]:
    """Where you (not the agent) can move a job from this status."""
    return [
        to
        for (frm, to), actor in ALLOWED_TRANSITIONS.items()
        if frm == status and actor == Actor.USER
    ]


def applied_today(session: Session) -> int:
    start = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    return session.exec(
        select(func.count())
        .select_from(JobEvent)
        .where(JobEvent.to_status == JobStatus.APPLIED, JobEvent.created_at >= start)
    ).one()


def waiting_too_long(session: Session, days: int = NO_RESPONSE_AFTER_DAYS) -> list[Job]:
    """Applied jobs with no news for `days` days. You decide whether to mark them."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    applied = session.exec(select(Job).where(Job.status == JobStatus.APPLIED)).all()
    return [j for j in applied if _aware(j.status_updated_at) <= cutoff]


def move(session: Session, job: Job, target: JobStatus, note: str | None = None) -> Job:
    """Move a job as the user. Raises TransitionError if the lifecycle doesn't allow it."""
    reason = f"you: {note.strip()}" if note and note.strip() else "you"
    if note and note.strip():
        job.notes = note.strip()
    return change_status(session, job, target, Actor.USER, reason=reason)


def check_letter(draft: Draft, letter: str, resume: MasterResume, job: Job) -> TruthReport:
    """Run the same honesty check on your edited letter. Results are advice only:
    you are the final judge of your own letter."""
    plan = {**(draft.plan or {}), "cover_letter": _body(letter)}
    report = check_draft(plan, resume, job)
    # The rule-based "name a gap" nudge is for the model, not for your own edits.
    report.problems = [p for p in report.problems if "doesn't name a gap" not in p]
    # Length is your call; show it as advice rather than a problem.
    length = [p for p in report.problems if " words; " in p]
    report.problems = [p for p in report.problems if p not in length]
    report.warnings = length + report.warnings
    return report


def _body(letter: str) -> str:
    """The letter without the greeting and sign-off the renderer adds."""
    lines = letter.strip().splitlines()
    if lines and lines[0].lower().startswith("dear"):
        lines = lines[1:]
    text = "\n".join(lines).strip()
    for sign_off in ("Kind regards,", "Best regards,", "Regards,", "Sincerely,"):
        if sign_off in text:
            text = text[: text.rfind(sign_off)].strip()
    return text


def save_letter(session: Session, draft: Draft, letter: str) -> Draft:
    """Save your edited letter. The agent's original is kept the first time you edit."""
    letter = letter.strip() + "\n"
    if draft.folder:
        folder = Path(draft.folder)
        folder.mkdir(parents=True, exist_ok=True)
        original = folder / "cover_letter.original.txt"
        if not original.exists():
            original.write_text(draft.cover_letter, encoding="utf-8")
        (folder / "cover_letter.txt").write_text(letter, encoding="utf-8")
    draft.cover_letter = letter
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft


def events(session: Session, job_id: int) -> list[JobEvent]:
    return session.exec(
        select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.created_at, JobEvent.id)
    ).all()
