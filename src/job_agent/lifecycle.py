"""Job statuses and the rules for moving between them.

The agent can only move a job as far as DRAFTED. Every move after that
needs the user. This mirrors the approved lifecycle diagram.
"""

from enum import StrEnum


class JobStatus(StrEnum):
    NEW = "new"
    FILTERED_OUT = "filtered_out"
    SHORTLISTED = "shortlisted"
    DRAFTED = "drafted"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    INTERVIEW = "interview"
    NO_RESPONSE = "no_response"
    OFFER = "offer"
    CLOSED = "closed"


class Actor(StrEnum):
    AGENT = "agent"
    USER = "user"


# (from, to) -> who is allowed to make that move
ALLOWED_TRANSITIONS: dict[tuple[JobStatus, JobStatus], Actor] = {
    (JobStatus.NEW, JobStatus.FILTERED_OUT): Actor.AGENT,
    (JobStatus.NEW, JobStatus.SHORTLISTED): Actor.AGENT,
    (JobStatus.SHORTLISTED, JobStatus.DRAFTED): Actor.AGENT,
    # Re-screening after you change config/search.yaml. Only before drafting.
    (JobStatus.SHORTLISTED, JobStatus.FILTERED_OUT): Actor.AGENT,
    (JobStatus.FILTERED_OUT, JobStatus.SHORTLISTED): Actor.AGENT,
    (JobStatus.DRAFTED, JobStatus.APPROVED): Actor.USER,
    (JobStatus.DRAFTED, JobStatus.REJECTED): Actor.USER,
    (JobStatus.APPROVED, JobStatus.APPLIED): Actor.USER,
    (JobStatus.APPLIED, JobStatus.INTERVIEW): Actor.USER,
    (JobStatus.APPLIED, JobStatus.NO_RESPONSE): Actor.USER,
    (JobStatus.INTERVIEW, JobStatus.OFFER): Actor.USER,
    (JobStatus.INTERVIEW, JobStatus.CLOSED): Actor.USER,
}

FINAL_STATUSES = {
    JobStatus.REJECTED,
    JobStatus.NO_RESPONSE,
    JobStatus.OFFER,
    JobStatus.CLOSED,
}


class TransitionError(Exception):
    """Raised when a status change breaks the lifecycle rules."""


def check_transition(current: JobStatus, target: JobStatus, actor: Actor) -> None:
    """Raise TransitionError unless `actor` may move a job from `current` to `target`."""
    required = ALLOWED_TRANSITIONS.get((current, target))
    if required is None:
        raise TransitionError(f"Cannot move a job from '{current}' to '{target}'.")
    if required != actor:
        raise TransitionError(
            f"Only the {required} can move a job from '{current}' to '{target}', not the {actor}."
        )
