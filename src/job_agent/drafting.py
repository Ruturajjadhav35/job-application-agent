"""Step 5: draft a tailored resume and cover letter for the best shortlisted jobs.

For each job:
1. Ask the LLM for a plan (fit notes, 2-line summary, 10 skills, bullet order, letter).
2. Fix the plan's structure in code (only real skills and real bullet ids survive).
3. Run the truthfulness check. If it fails, send the problems back once for a rewrite.
4. If it passes: save files, record the draft, move the job to DRAFTED.
   If it still fails: save the draft for you to inspect, leave the job SHORTLISTED.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, time

from sqlalchemy import func
from sqlmodel import Session, select

from job_agent.config import SearchConfig, get_settings
from job_agent.db import change_status
from job_agent.lifecycle import Actor, JobStatus
from job_agent.llm import LLM, LLMError, Usage
from job_agent.models import Draft, Job
from job_agent.prompts import RETRY, SYSTEM, task_prompt
from job_agent.render import render_cover_letter, render_notes, render_resume_tex, slug, write_files
from job_agent.resume import MasterResume
from job_agent.schemas import _normalise, _normalise_company
from job_agent.text_utils import short_company
from job_agent.truthfulness import TruthReport, check_draft

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


@dataclass
class DraftStats:
    drafted: int = 0
    failed: int = 0
    errors: int = 0
    duplicates: int = 0
    usage: Usage = field(default_factory=Usage)
    lines: list[str] = field(default_factory=list)

    def report(self) -> str:
        head = (
            f"Drafted {self.drafted}, failed honesty check {self.failed}, "
            f"errors {self.errors}, same-job duplicates set aside {self.duplicates}."
        )
        return "\n".join([head, *self.lines, str(self.usage)])


# ---------- structure fixing ----------


def normalise_plan(plan: dict, resume: MasterResume) -> dict:
    """Keep only real skills and real bullet/project ids, whatever the model returned."""
    names = resume.skill_names()
    by_lower = {n.lower(): n for n in names}
    skills: list[str] = []
    for s in plan.get("skills", []):
        real = by_lower.get(str(s).strip().lower())
        if real and real not in skills:
            skills.append(real)
    for n in names:  # top up to exactly 10 from the resume's own order
        if len(skills) >= 10:
            break
        if n not in skills:
            skills.append(n)
    plan["skills"] = skills[:10]

    order = plan.get("experience_order") or {}
    fixed = {}
    for i, e in enumerate(resume.experience, 1):
        valid = [f"E{i}.{j}" for j in range(1, len(e.bullets) + 1)]
        chosen = [b for b in order.get(f"E{i}", []) if b in valid]
        chosen = list(dict.fromkeys(chosen))
        if len(chosen) < min(2, len(valid)):
            chosen = valid
        fixed[f"E{i}"] = chosen
    plan["experience_order"] = fixed

    valid_p = [f"P{i}" for i in range(1, len(resume.projects) + 1)]
    projects = list(dict.fromkeys(p for p in plan.get("projects", []) if p in valid_p))[:3]
    plan["projects"] = projects or valid_p[:2]

    plan["summary"] = str(plan.get("summary", "")).strip() or resume.summary
    plan["cover_letter"] = str(plan.get("cover_letter", "")).strip()
    fit = plan.get("fit") if isinstance(plan.get("fit"), dict) else {}
    plan["fit"] = {
        "summary": str(fit.get("summary", "")),
        "strengths": [str(x) for x in fit.get("strengths", [])][:4],
        "gaps": [str(x) for x in fit.get("gaps", [])][:4],
    }
    return plan


# ---------- choosing jobs ----------


def group_key(job: Job) -> tuple[str, str]:
    return _normalise_company(job.company), _normalise(job.title)


def drafted_today(session: Session) -> int:
    start = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    return session.exec(
        select(func.count()).select_from(Draft).where(Draft.passed, Draft.created_at >= start)
    ).one()


def pick_jobs(
    session: Session, top: int, job_id: int | None, stats: DraftStats, redraft: bool = False
):
    """Best-scoring shortlisted jobs without a passed draft.

    The same job posted in other cities is set aside, so you apply once.
    """
    if job_id is not None:
        job = session.get(Job, job_id)
        if not job:
            raise ValueError(f"No job with id {job_id}.")
        allowed = {JobStatus.SHORTLISTED} | ({JobStatus.DRAFTED} if redraft else set())
        if job.status not in allowed:
            hint = " Add --redraft to draft it again." if job.status == JobStatus.DRAFTED else ""
            raise ValueError(f"Job {job_id} is '{job.status}', so it can't be drafted.{hint}")
        return [(job, [])]

    has_draft = select(Draft.job_id).where(Draft.passed)
    candidates = session.exec(
        select(Job)
        .where(
            Job.status == JobStatus.SHORTLISTED,
            Job.match_score.is_not(None),
            Job.id.not_in(has_draft),
        )
        .order_by(Job.match_score.desc())
    ).all()

    picked: dict[tuple, tuple[Job, list[str]]] = {}
    for job in candidates:
        key = group_key(job)
        if key in picked:
            first, others = picked[key]
            others.append(job.city or "?")
            reason = f"same job as #{first.id} (posted in another city)"
            job.notes = reason
            change_status(session, job, JobStatus.FILTERED_OUT, Actor.AGENT, reason=reason)
            stats.duplicates += 1
            continue
        if len(picked) < top:
            picked[key] = (job, [])
    return list(picked.values())


# ---------- drafting one job ----------


def draft_job(
    session: Session,
    job: Job,
    also_in: list[str],
    resume: MasterResume,
    llm: LLM,
    stats: DraftStats,
) -> Draft | None:
    prompt = task_prompt(resume, job)
    usage = Usage()
    plan: dict = {}
    report = TruthReport()
    attempts = 0
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        try:
            resp = llm.complete(SYSTEM, prompt)
            usage.add(resp.usage)
            plan = normalise_plan(resp.json(), resume)
        except LLMError as e:
            usage_note = f" ({usage})" if usage.calls else ""
            stats.lines.append(f"  [{job.id}] {job.title} | {job.company}: error: {e}{usage_note}")
            stats.errors += 1
            stats.usage.add(usage)
            if "rate limit" in str(e):
                raise
            return None
        report = check_draft(plan, resume, job)
        if report.passed:
            break
        problems = "\n".join(f"- {p}" for p in report.problems)
        prompt = task_prompt(resume, job) + "\n\n" + RETRY.format(problems=problems)
    stats.usage.add(usage)

    resume_tex = render_resume_tex(resume, plan)
    letter = render_cover_letter(resume, job, plan["cover_letter"])
    notes = render_notes(job, plan, report.as_dict(), llm.name, also_in)
    settings = get_settings()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    company = slug(short_company(job.company))
    base = settings.drafts_dir / f"{job.id}-{company}-{slug(job.title)}-{stamp}"
    folder, n = base, 2
    while folder.exists():  # never overwrite an earlier draft
        folder, n = base.with_name(f"{base.name}-{n}"), n + 1
    write_files(folder, resume_tex, letter, notes)

    draft = Draft(
        job_id=job.id,
        model=llm.name,
        attempts=attempts,
        passed=report.passed,
        folder=str(folder),
        plan=plan,
        truth_report=report.as_dict(),
        resume_tex=resume_tex,
        cover_letter=letter,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
    )
    session.add(draft)
    session.commit()

    label = f"  [{job.id}] {job.title} | {job.company}"
    if report.passed:
        if job.status == JobStatus.SHORTLISTED:  # a re-draft leaves the status as it is
            change_status(
                session,
                job,
                JobStatus.DRAFTED,
                Actor.AGENT,
                reason=f"draft #{draft.id} passed truthfulness check ({attempts} attempt(s))",
            )
        stats.drafted += 1
        stats.lines.append(f"{label}: drafted ({attempts} attempt(s)) -> {folder.name}")
    else:
        job.notes = f"draft #{draft.id} failed truthfulness check"
        session.add(job)
        session.commit()
        stats.failed += 1
        stats.lines.append(
            f"{label}: FAILED honesty check after {attempts} attempts. See {folder.name}/notes.md"
        )
    return draft


def draft_jobs(
    session: Session,
    search: SearchConfig,
    resume: MasterResume,
    llm: LLM,
    top: int = 5,
    job_id: int | None = None,
    redraft: bool = False,
) -> DraftStats:
    stats = DraftStats()
    remaining = search.daily_application_cap - drafted_today(session)
    if remaining <= 0 and job_id is None:
        stats.lines.append(
            f"Daily cap of {search.daily_application_cap} drafts reached. "
            "Try again tomorrow or raise daily_application_cap."
        )
        return stats
    top = min(top, max(remaining, 0)) if job_id is None else 1
    for job, also_in in pick_jobs(session, top, job_id, stats, redraft=redraft):
        try:
            draft_job(session, job, also_in, resume, llm, stats)
        except LLMError:
            stats.lines.append("Stopped: the LLM rate limit was reached. Run again later.")
            break
    return stats
