"""Database tables."""

from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel

from job_agent.lifecycle import Actor, JobStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


def _enum_column(enum_cls, name: str, **kwargs) -> Column:
    # Stored as text with a CHECK constraint, so the database itself rejects unknown values.
    return Column(
        SAEnum(
            enum_cls,
            name=name,
            native_enum=False,
            create_constraint=True,
            values_callable=lambda e: [m.value for m in e],
            validate_strings=True,
        ),
        **kwargs,
    )


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: int | None = Field(default=None, primary_key=True)
    dedup_key: str = Field(index=True, unique=True, max_length=64)

    title: str
    company: str = Field(index=True)
    city: str | None = Field(default=None, index=True)
    is_remote: bool = False
    salary_min_inr: int | None = None
    salary_max_inr: int | None = None
    experience_min_years: int | None = None
    experience_max_years: int | None = None
    description: str = ""
    apply_url: str
    source: str = Field(index=True)
    source_job_id: str | None = None
    posted_at: datetime | None = None

    status: JobStatus = Field(
        default=JobStatus.NEW,
        sa_column=_enum_column(JobStatus, "job_status", nullable=False, index=True),
    )
    match_score: float | None = Field(default=None, index=True)
    match_details: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    scam_flags: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    notes: str | None = None

    fetched_at: datetime = Field(default_factory=utcnow)
    status_updated_at: datetime = Field(default_factory=utcnow)


class JobEvent(SQLModel, table=True):
    """One row per status change: the lifecycle part of the audit log."""

    __tablename__ = "job_events"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", index=True)
    from_status: JobStatus | None = Field(
        default=None, sa_column=_enum_column(JobStatus, "event_from_status", nullable=True)
    )
    to_status: JobStatus = Field(
        sa_column=_enum_column(JobStatus, "event_to_status", nullable=False)
    )
    actor: Actor = Field(sa_column=_enum_column(Actor, "event_actor", nullable=False))
    reason: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
