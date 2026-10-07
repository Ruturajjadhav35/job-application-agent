"""Database engine, sessions and the few write operations the rest of the app needs."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from job_agent.config import get_settings
from job_agent.lifecycle import Actor, JobStatus, check_transition
from job_agent.models import Job, JobEvent, utcnow
from job_agent.schemas import JobPosting


@event.listens_for(Engine, "connect")
def _sqlite_foreign_keys(dbapi_conn, _record):
    # SQLite ignores foreign keys unless you switch them on per connection.
    if dbapi_conn.__class__.__module__.startswith("sqlite3"):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")


def make_engine(url: str | None = None):
    url = url or get_settings().database_url
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url)


engine = make_engine()


def init_db(eng=None) -> None:
    """Create tables directly. Fine for tests; use Alembic migrations for your real database."""
    SQLModel.metadata.create_all(eng or engine)


@contextmanager
def get_session(eng=None) -> Iterator[Session]:
    with Session(eng or engine) as session:
        yield session


def upsert_job(session: Session, posting: JobPosting) -> tuple[Job, bool]:
    """Insert a new job, or return the existing one if it's a duplicate.

    Returns (job, created). Duplicates are not overwritten, so a job's status
    and your notes are never reset by a later fetch.
    """
    existing = session.exec(select(Job).where(Job.dedup_key == posting.dedup_key)).first()
    if existing:
        return existing, False

    data = posting.model_dump()
    data["apply_url"] = str(posting.apply_url)
    job = Job(**data, dedup_key=posting.dedup_key)
    session.add(job)
    session.flush()  # assigns job.id
    session.add(
        JobEvent(
            job_id=job.id,
            from_status=None,
            to_status=JobStatus.NEW,
            actor=Actor.AGENT,
            reason=f"fetched from {posting.source}",
        )
    )
    session.commit()
    session.refresh(job)
    return job, True


def change_status(
    session: Session, job: Job, target: JobStatus, actor: Actor, reason: str | None = None
) -> Job:
    """The only function that should ever change a job's status."""
    check_transition(job.status, target, actor)
    session.add(
        JobEvent(
            job_id=job.id, from_status=job.status, to_status=target, actor=actor, reason=reason
        )
    )
    job.status = target
    job.status_updated_at = utcnow()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job
