"""Run the step 3 filters over jobs in the database and record every decision."""

from collections import Counter
from dataclasses import dataclass, field

from sqlmodel import Session, select

from job_agent.config import SearchConfig
from job_agent.db import change_status
from job_agent.filters import screen
from job_agent.lifecycle import Actor, JobStatus
from job_agent.models import Job


@dataclass
class ScreenStats:
    checked: int = 0
    shortlisted: int = 0
    filtered_out: int = 0
    unchanged: int = 0
    reasons: Counter = field(default_factory=Counter)

    def report(self) -> str:
        lines = [
            f"Checked {self.checked} jobs: {self.shortlisted} shortlisted, "
            f"{self.filtered_out} filtered out, {self.unchanged} unchanged."
        ]
        if self.reasons:
            lines.append("Why jobs were filtered out (a job can have several reasons):")
            for reason, n in self.reasons.most_common():
                lines.append(f"  {reason:<20}{n}")
        return "\n".join(lines)


def screen_jobs(
    session: Session, search: SearchConfig, rescreen: bool = False, dry_run: bool = False
) -> ScreenStats:
    """Screen NEW jobs. With rescreen=True, also re-check SHORTLISTED and FILTERED_OUT jobs."""
    statuses = [JobStatus.NEW]
    if rescreen:
        statuses += [JobStatus.SHORTLISTED, JobStatus.FILTERED_OUT]
    jobs = session.exec(select(Job).where(Job.status.in_(statuses))).all()

    stats = ScreenStats()
    for job in jobs:
        stats.checked += 1
        result = screen(job, search)
        for r in result.reasons:
            stats.reasons[r.split(":")[0]] += 1
        target = JobStatus.SHORTLISTED if result.shortlist else JobStatus.FILTERED_OUT

        if job.status == target:
            stats.unchanged += 1
            continue
        if target == JobStatus.SHORTLISTED:
            stats.shortlisted += 1
        else:
            stats.filtered_out += 1
        if dry_run:
            continue

        job.scam_flags = result.scam_flags
        job.notes = result.summary()
        change_status(session, job, target, Actor.AGENT, reason=result.summary())
    return stats
