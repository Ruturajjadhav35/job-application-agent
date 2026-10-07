"""Run a source and save what it finds."""

import logging
from dataclasses import dataclass

from sqlmodel import Session

from job_agent.config import SearchConfig
from job_agent.db import upsert_job
from job_agent.sources.base import JobSource

log = logging.getLogger(__name__)


@dataclass
class IngestStats:
    source: str
    fetched: int = 0
    new: int = 0
    duplicates: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return (
            f"{self.source}: {self.fetched} fetched, {self.new} new, "
            f"{self.duplicates} duplicates, {self.skipped} skipped as unusable"
        )


def ingest(source: JobSource, search: SearchConfig, session: Session) -> IngestStats:
    stats = IngestStats(source=source.name)
    for posting in source.fetch(search):
        stats.fetched += 1
        _, created = upsert_job(session, posting)
        if created:
            stats.new += 1
        else:
            stats.duplicates += 1
    stats.skipped = getattr(source, "skipped", 0)
    return stats
