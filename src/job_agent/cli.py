"""Command line entry point: `uv run job-agent <command>`."""

import argparse
import logging
import sys

from sqlalchemy import func
from sqlmodel import select

from job_agent.config import get_settings, load_search_config
from job_agent.db import get_session
from job_agent.lifecycle import JobStatus
from job_agent.models import Job, JobEvent
from job_agent.sources.base import SourceConfigError


def show_config(_args) -> None:
    print(load_search_config().model_dump_json(indent=2))


def show_stats(_args) -> None:
    with get_session() as session:
        rows = session.exec(select(Job.status, func.count()).group_by(Job.status)).all()
        by_source = session.exec(select(Job.source, func.count()).group_by(Job.source)).all()
    if not rows:
        print("No jobs yet.")
        return
    print("By status:")
    for status, count in rows:
        print(f"  {status:<14}{count}")
    print("By source:")
    for source, count in by_source:
        print(f"  {source:<14}{count}")


def fetch(args) -> None:
    from job_agent.ingest import ingest
    from job_agent.sources.adzuna import AdzunaSource

    search = load_search_config()
    try:
        source = AdzunaSource(max_queries=args.max_queries)
    except SourceConfigError as e:
        sys.exit(f"Error: {e}")

    queries = source.build_queries(search)
    print(f"Running {len(queries)} Adzuna searches (about 3 seconds apart)...")

    if args.dry_run:
        try:
            for i, p in enumerate(source.fetch(search), 1):
                where = "Remote" if p.is_remote and not p.city else (p.city or "?")
                print(f"{i:>3}. {p.title} | {p.company} | {where}")
        except SourceConfigError as e:
            sys.exit(f"Error: {e}")
        print("Dry run: nothing was saved.")
        return

    try:
        with get_session() as session:
            stats = ingest(source, search, session)
    except SourceConfigError as e:
        sys.exit(f"Error: {e}")
    print(stats)


def run_screen(args) -> None:
    from job_agent.screening import screen_jobs

    search = load_search_config()
    with get_session() as session:
        stats = screen_jobs(session, search, rescreen=args.rescreen, dry_run=args.dry_run)
    print(stats.report())
    if args.dry_run:
        print("Dry run: no statuses were changed.")


def list_jobs(args) -> None:
    status = JobStatus.SHORTLISTED if args.top else JobStatus(args.status)
    limit = args.top or args.limit
    with get_session() as session:
        query = select(Job).where(Job.status == status)
        if args.source:
            query = query.where(Job.source == args.source)
        if args.top:
            query = query.order_by(Job.match_score.desc().nulls_last())
        else:
            query = query.order_by(Job.posted_at.desc())
        jobs = session.exec(query.limit(limit)).all()
    if not jobs:
        print(f"No jobs with status '{status}'.")
    for j in jobs:
        where = j.city or ("Remote" if j.is_remote else "?")
        score = f"{j.match_score:5.1f}  " if j.match_score is not None else ""
        print(f"[{j.id:>4}] {score}{j.title} | {j.company} | {where}")
        if args.reasons and j.notes:
            print(f"       {j.notes}")
        if args.reasons and j.match_details:
            skills = ", ".join(j.match_details.get("matched_skills", [])) or "none found"
            print(f"       skills found: {skills}")


def run_match(args) -> None:
    from job_agent.config import get_settings
    from job_agent.matching import LocalEmbedder, Matcher
    from job_agent.ranking import match_jobs
    from job_agent.resume import load_resume

    search = load_search_config()
    try:
        resume = load_resume(get_settings().master_resume_path)
    except FileNotFoundError as e:
        sys.exit(f"Error: {e}")
    print("Loading the matching model (the first run downloads it, about 130 MB)...")
    matcher = Matcher(resume, search, LocalEmbedder())
    with get_session() as session:
        stats = match_jobs(session, search, matcher, rescore=args.rescore, dry_run=args.dry_run)
    print(stats.report(search.min_match_score))
    if args.dry_run:
        print("Dry run: nothing was saved.")


def why(args) -> None:
    with get_session() as session:
        job = session.get(Job, args.job_id)
        if not job:
            sys.exit(f"No job with id {args.job_id}.")
        events = session.exec(
            select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.created_at)
        ).all()
    print(f"{job.title} | {job.company} | {job.city or '?'}  (status: {job.status})")
    print(f"Apply: {job.apply_url}")
    if job.scam_flags:
        print(f"Scam flags: {', '.join(job.scam_flags)}")
    if job.match_score is not None:
        d = job.match_details or {}
        print(f"Match score: {job.match_score:g}")
        print(f"  Skills found: {', '.join(d.get('matched_skills', [])) or 'none'}")
        if d.get("missing_must_haves"):
            print(f"  Must-haves not found: {', '.join(d['missing_must_haves'])}")
    print("History:")
    for e in events:
        frm = e.from_status or "-"
        print(
            f"  {e.created_at:%Y-%m-%d %H:%M}  {frm} -> {e.to_status}  ({e.actor}) {e.reason or ''}"
        )


def main() -> None:
    logging.basicConfig(
        level=get_settings().log_level, format="%(levelname)s %(message)s", stream=sys.stdout
    )
    # httpx logs full URLs, which include API keys. Keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(prog="job-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("config", help="show your search settings").set_defaults(func=show_config)
    sub.add_parser("stats", help="count jobs by status and source").set_defaults(func=show_stats)
    f = sub.add_parser("fetch", help="fetch new jobs from Adzuna")
    f.add_argument("--dry-run", action="store_true", help="print jobs without saving them")
    f.add_argument("--max-queries", type=int, default=None, help="only run the first N searches")
    f.set_defaults(func=fetch)

    sc = sub.add_parser("screen", help="shortlist or filter out new jobs")
    sc.add_argument(
        "--rescreen",
        action="store_true",
        help="also re-check shortlisted and filtered-out jobs (after editing search.yaml)",
    )
    sc.add_argument("--dry-run", action="store_true", help="show the result without saving")
    sc.set_defaults(func=run_screen)

    ls = sub.add_parser("list", help="list jobs by status")
    ls.add_argument("--status", default="shortlisted", choices=[s.value for s in JobStatus])
    ls.add_argument("--limit", type=int, default=20)
    ls.add_argument("--source", default=None)
    ls.add_argument("--reasons", action="store_true", help="show why each job got its status")
    ls.add_argument("--top", type=int, default=None, help="best N shortlisted jobs by match score")
    ls.set_defaults(func=list_jobs)

    m = sub.add_parser("match", help="score shortlisted jobs against your master resume")
    m.add_argument(
        "--rescore",
        action="store_true",
        help="re-score everything (after changing your resume or min_match_score)",
    )
    m.add_argument("--dry-run", action="store_true", help="show scores without saving")
    m.set_defaults(func=run_match)

    w = sub.add_parser("why", help="show a job's details and full status history")
    w.add_argument("job_id", type=int)
    w.set_defaults(func=why)

    args = parser.parse_args()
    args.func(args)
