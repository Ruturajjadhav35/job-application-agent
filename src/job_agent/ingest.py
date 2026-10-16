"""Run a source and save what it finds."""

import logging
from dataclasses import dataclass

from sqlmodel import Session

from job_agent.config import SearchConfig
from job_agent.db import change_status, enrich_job, upsert_job
from job_agent.filters import screen
from job_agent.lifecycle import Actor, JobStatus
from job_agent.sources.base import JobSource

log = logging.getLogger(__name__)


@dataclass
class IngestStats:
    source: str
    fetched: int = 0
    new: int = 0
    duplicates: int = 0
    enriched: int = 0
    rechecked_out: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return (
            f"{self.source}: {self.fetched} fetched, {self.new} new, "
            f"{self.duplicates} duplicates, {self.skipped} skipped as unusable"
            + (f", {self.enriched} upgraded with the full description" if self.enriched else "")
            + (f" ({self.rechecked_out} of those now filtered out)" if self.rechecked_out else "")
        )


def ingest(source: JobSource, search: SearchConfig, session: Session) -> IngestStats:
    stats = IngestStats(source=source.name)
    for posting in source.fetch(search):
        stats.fetched += 1
        job, created = upsert_job(session, posting)
        if created:
            stats.new += 1
            continue
        stats.duplicates += 1
        if enrich_job(session, job, posting):
            stats.enriched += 1
            # A shortlisted job may now fail a rule the short summary hid (e.g. "5+ years").
            if job.status == JobStatus.SHORTLISTED:
                result = screen(job, search)
                if not result.shortlist:
                    reason = "re-checked with full description: " + result.summary()
                    job.notes = reason
                    change_status(session, job, JobStatus.FILTERED_OUT, Actor.AGENT, reason=reason)
                    stats.rechecked_out += 1
    stats.skipped = getattr(source, "skipped", 0)
    return stats
