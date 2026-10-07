"""Run step 4 matching over the database and record the results."""

from dataclasses import dataclass, field

from sqlalchemy import or_
from sqlmodel import Session, select

from job_agent.config import SearchConfig
from job_agent.db import change_status
from job_agent.lifecycle import Actor, JobStatus
from job_agent.matching import Matcher
from job_agent.models import Job

LOW_MATCH = "low match"
BANDS = [(80, 101), (60, 80), (40, 60), (20, 40), (0, 20)]


@dataclass
class MatchStats:
    scored: int = 0
    kept: int = 0
    filtered_out: int = 0
    restored: int = 0
    bands: dict[str, int] = field(default_factory=dict)
    top: list[tuple[float, str]] = field(default_factory=list)
    bottom: list[tuple[float, str]] = field(default_factory=list)

    def report(self, cutoff: float) -> str:
        lines = [
            f"Scored {self.scored} jobs (cut-off {cutoff:g}): {self.kept} shortlisted, "
            f"{self.filtered_out} moved to filtered out as low match, {self.restored} restored.",
            "Score spread:",
        ]
        for lo, hi in BANDS:
            label = f"{lo}-{min(hi, 100)}"
            n = self.bands.get(label, 0)
            lines.append(f"  {label:>7}  {n:>4}  {'#' * min(n // 5, 60)}")
        if self.top:
            lines.append("Highest scores:")
            lines += [f"  {s:5.1f}  {t}" for s, t in self.top]
        if self.bottom:
            lines.append("Lowest scores:")
            lines += [f"  {s:5.1f}  {t}" for s, t in self.bottom]
        return "\n".join(lines)


def _band(score: float) -> str:
    for lo, hi in BANDS:
        if lo <= score < hi:
            return f"{lo}-{min(hi, 100)}"
    return "0-20"


def match_jobs(
    session: Session,
    search: SearchConfig,
    matcher: Matcher,
    rescore: bool = False,
    dry_run: bool = False,
) -> MatchStats:
    """Score shortlisted jobs that have no score yet.

    rescore=True re-scores every shortlisted job and every job filtered out
    only for low match, so changing min_match_score can bring jobs back.
    """
    cutoff = search.min_match_score
    if rescore:
        query = select(Job).where(
            or_(
                Job.status == JobStatus.SHORTLISTED,
                (Job.status == JobStatus.FILTERED_OUT) & Job.notes.startswith(LOW_MATCH),
            )
        )
    else:
        query = select(Job).where(Job.status == JobStatus.SHORTLISTED, Job.match_score.is_(None))
    jobs = session.exec(query).all()

    stats = MatchStats()
    results = matcher.score_many(jobs)
    ranked = sorted(zip(results, jobs, strict=True), key=lambda x: -x[0].score)
    stats.top = [(r.score, f"{j.title} | {j.company}") for r, j in ranked[:5]]
    stats.bottom = [(r.score, f"{j.title} | {j.company}") for r, j in ranked[-5:]][::-1]

    for result, job in zip(results, jobs, strict=True):
        stats.scored += 1
        label = _band(result.score)
        stats.bands[label] = stats.bands.get(label, 0) + 1
        passes = result.score >= cutoff

        if job.status == JobStatus.SHORTLISTED and not passes:
            stats.filtered_out += 1
        elif job.status == JobStatus.FILTERED_OUT and passes:
            stats.restored += 1
        elif passes:
            stats.kept += 1
        if dry_run:
            continue

        job.match_score = result.score
        job.match_details = result.details()
        reason = f"{LOW_MATCH}: {result.score:g} (cut-off {cutoff:g})"
        if job.status == JobStatus.SHORTLISTED and not passes:
            job.notes = reason
            change_status(session, job, JobStatus.FILTERED_OUT, Actor.AGENT, reason=reason)
        elif job.status == JobStatus.FILTERED_OUT and passes:
            job.notes = f"match score {result.score:g}"
            change_status(session, job, JobStatus.SHORTLISTED, Actor.AGENT, reason=job.notes)
        else:
            if job.status == JobStatus.FILTERED_OUT:
                job.notes = reason
            session.add(job)
    if not dry_run:
        session.commit()
    return stats
