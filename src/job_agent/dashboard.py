"""Step 6: the review dashboard. Start it with `uv run job-agent dashboard`.

Layout only. All rules live in review.py. It runs on your own machine
(127.0.0.1) and never submits anything for you.
"""

import html
import os
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from sqlmodel import Session

from job_agent.config import get_settings, load_search_config
from job_agent.db import make_engine
from job_agent.lifecycle import JobStatus, TransitionError
from job_agent.models import Job
from job_agent.resume import load_resume
from job_agent.review import (
    NO_RESPONSE_AFTER_DAYS,
    applied_today,
    check_letter,
    events,
    latest_draft,
    move,
    pipeline_counts,
    queue,
    save_letter,
    user_moves,
    waiting_too_long,
)
from job_agent.text_utils import short_company

LABELS = {
    JobStatus.SHORTLISTED: "Shortlisted",
    JobStatus.DRAFTED: "To review",
    JobStatus.APPROVED: "Approved",
    JobStatus.APPLIED: "Applied",
    JobStatus.INTERVIEW: "Interview",
    JobStatus.OFFER: "Offer",
    JobStatus.REJECTED: "Rejected by you",
    JobStatus.NO_RESPONSE: "No response",
    JobStatus.CLOSED: "Closed",
}
BUTTON_TEXT = {
    JobStatus.APPROVED: "Approve",
    JobStatus.REJECTED: "Reject",
    JobStatus.APPLIED: "I've submitted it",
    JobStatus.INTERVIEW: "Got an interview",
    JobStatus.NO_RESPONSE: "No response",
    JobStatus.OFFER: "Got an offer",
    JobStatus.CLOSED: "Closed",
}


@st.cache_resource
def engine():
    return make_engine(os.environ.get("JOB_AGENT_DB_URL") or None)


@st.cache_resource
def resume():
    try:
        return load_resume(get_settings().master_resume_path)
    except FileNotFoundError:
        return None


def where(job: Job) -> str:
    return job.city or ("Remote" if job.is_remote else "location not stated")


def option_label(job: Job) -> str:
    score = f"{job.match_score:.0f}" if job.match_score is not None else "--"
    return f"{score} · {job.title} · {short_company(job.company)} · {where(job)}"


def overleaf_button(tex: str, key: str) -> None:
    """Opens the resume in Overleaf in a new tab, where it compiles to PDF."""
    components.html(
        f"""<form action="https://www.overleaf.com/docs" method="post" target="_blank">
        <input type="hidden" name="snip" value="{html.escape(tex, quote=True)}">
        <input type="hidden" name="snip_name" value="resume.tex">
        <button type="submit" style="font:inherit;padding:6px 12px;border-radius:6px;
        border:1px solid #bbb;background:#fff;cursor:pointer">Open resume in Overleaf</button>
        </form>""",
        height=44,
    )


def do_move(session: Session, job: Job, target: JobStatus, note: str | None = None) -> None:
    try:
        move(session, job, target, note)
        st.session_state["flash"] = f"Moved '{job.title}' to {LABELS[target]}."
    except TransitionError as e:
        st.session_state["flash"] = f"Not allowed: {e}"
    st.rerun()


def draft_files(job: Job, session: Session, key: str) -> None:
    draft = latest_draft(session, job.id)
    if not draft:
        st.caption("No passed draft for this job.")
        return
    c1, c2, c3 = st.columns(3)
    c1.download_button(
        "Cover letter (.txt)",
        draft.cover_letter,
        file_name=f"cover_letter_{job.id}.txt",
        key=f"dl_l_{key}",
    )
    c2.download_button(
        "Resume (.tex)", draft.resume_tex, file_name=f"resume_{job.id}.tex", key=f"dl_r_{key}"
    )
    with c3:
        overleaf_button(draft.resume_tex, key)


# ---------------------------------------------------------------- tabs


def review_tab(session: Session) -> None:
    items = queue(session, JobStatus.DRAFTED)
    if not items:
        st.info("Nothing to review. Draft some jobs with `uv run job-agent draft --top 3`.")
        return
    by_id = {i.job.id: i for i in items}
    job_id = st.selectbox(
        "Job", list(by_id), format_func=lambda i: option_label(by_id[i].job), key="review_pick"
    )
    item = by_id[job_id]
    job, draft = item.job, item.draft

    st.subheader(f"{job.title} at {short_company(job.company)}")
    st.caption(
        f"{job.company} · {where(job)} · match score {job.match_score} · source {job.source}"
    )
    st.link_button("Open the job posting", job.apply_url)
    st.caption(
        "Read the full posting before approving. The draft was written from a short summary of it."
    )

    if not draft:
        st.warning(
            "This job has no passed draft. Re-draft it with "
            f"`uv run job-agent draft --job {job.id} --redraft`."
        )
        return

    fit = (draft.plan or {}).get("fit", {})
    left, right = st.columns(2)
    with left:
        st.markdown("**Strengths**")
        for s in fit.get("strengths", []) or ["(none listed)"]:
            st.markdown(f"- {s}")
    with right:
        st.markdown("**Gaps**")
        for g in fit.get("gaps", []) or ["(none listed)"]:
            st.markdown(f"- {g}")
    for w in (draft.truth_report or {}).get("warnings", []):
        st.caption(f"Style note: {w}")

    st.markdown(
        "**Cover letter**  \nEdit freely. Your version is saved next to the agent's original."
    )
    letter = st.text_area(
        "Cover letter",
        draft.cover_letter,
        height=380,
        key=f"letter_{draft.id}",
        label_visibility="collapsed",
    )
    edited = letter.strip() != draft.cover_letter.strip()

    c1, c2 = st.columns(2)
    if c1.button("Check my edits", key=f"check_{draft.id}", disabled=not edited):
        r = resume()
        if r is None:
            st.warning("Can't find private/master_resume.json, so the check can't run.")
        else:
            report = check_letter(draft, letter, r, job)
            if report.passed:
                st.success("No problems found against your master resume.")
            for p in report.problems:
                st.warning(p)
    if c2.button("Save letter", key=f"save_{draft.id}", disabled=not edited):
        save_letter(session, draft, letter)
        st.session_state["flash"] = "Letter saved."
        st.rerun()

    st.markdown("**Resume**")
    draft_files(job, session, f"rev{draft.id}")
    with st.expander("Skills and summary chosen for this job"):
        st.write((draft.plan or {}).get("summary", ""))
        st.write(", ".join((draft.plan or {}).get("skills", [])))

    st.divider()
    if edited:
        st.warning("You have unsaved edits. Save the letter before approving.")
    note = st.text_input("Note (optional, e.g. why you're rejecting it)", key=f"note_{job.id}")
    a, b = st.columns(2)
    if a.button("Approve", type="primary", key=f"approve_{job.id}", disabled=edited):
        do_move(session, job, JobStatus.APPROVED, note)
    if b.button("Reject", key=f"reject_{job.id}"):
        do_move(session, job, JobStatus.REJECTED, note)


def submit_tab(session: Session) -> None:
    cap = load_search_config().daily_application_cap
    done = applied_today(session)
    st.caption(f"Submitted today: {done} of your daily cap of {cap}.")
    if done >= cap:
        st.warning(
            "You've reached your daily cap. Quality beats volume; consider stopping here for today."
        )
    items = queue(session, JobStatus.APPROVED)
    if not items:
        st.info("No approved jobs waiting. Approve drafts in the 'To review' tab.")
        return
    for item in items:
        job = item.job
        with st.container(border=True):
            st.markdown(f"**{job.title}** at {short_company(job.company)} · {where(job)}")
            st.link_button("Open the application page", job.apply_url)
            draft_files(job, session, f"sub{job.id}")
            st.caption("Submit it yourself on the company's site, then record it here.")
            c1, c2 = st.columns(2)
            if c1.button("I've submitted it", type="primary", key=f"applied_{job.id}"):
                do_move(session, job, JobStatus.APPLIED)
            if c2.button("I won't apply", key=f"unapprove_{job.id}"):
                do_move(session, job, JobStatus.REJECTED, "decided not to apply after approving")


def track_tab(session: Session) -> None:
    stale = {j.id for j in waiting_too_long(session)}
    if stale:
        st.warning(
            f"{len(stale)} application(s) with no news for {NO_RESPONSE_AFTER_DAYS}+ "
            "days. Mark them 'No response' if you've heard nothing."
        )
    shown = False
    for status in (JobStatus.APPLIED, JobStatus.INTERVIEW):
        for item in queue(session, status):
            shown = True
            job = item.job
            with st.container(border=True):
                flag = "  ⏳ waiting a while" if job.id in stale else ""
                st.markdown(
                    f"**{job.title}** at {short_company(job.company)} · "
                    f"{LABELS[status]} since {job.status_updated_at:%d %b}{flag}"
                )
                cols = st.columns(len(user_moves(status)) or 1)
                for col, target in zip(cols, user_moves(status), strict=False):
                    if col.button(BUTTON_TEXT[target], key=f"{target}_{job.id}"):
                        do_move(session, job, target)
    if not shown:
        st.info("No applications in progress yet.")


def history_tab(session: Session) -> None:
    finished = [
        i
        for s in (JobStatus.OFFER, JobStatus.CLOSED, JobStatus.NO_RESPONSE, JobStatus.REJECTED)
        for i in queue(session, s)
    ]
    if not finished:
        st.info("Nothing finished yet.")
        return
    by_id = {i.job.id: i.job for i in finished}
    job_id = st.selectbox(
        "Job",
        list(by_id),
        key="hist_pick",
        format_func=lambda i: f"{LABELS[by_id[i].status]} · {option_label(by_id[i])}",
    )
    for e in events(session, job_id):
        frm = LABELS.get(e.from_status, e.from_status or "-") if e.from_status else "-"
        st.markdown(
            f"- {e.created_at:%d %b %H:%M} · {frm} → {LABELS.get(e.to_status, e.to_status)}"
            f" · {e.actor} · {e.reason or ''}"
        )


# ---------------------------------------------------------------- page


def main() -> None:
    st.set_page_config(page_title="Job Agent Review", layout="wide")
    st.title("Job applications")
    st.caption("The agent drafts. You review, approve and submit. Nothing is sent for you.")

    with Session(engine()) as session:
        counts = pipeline_counts(session)
        with st.sidebar:
            st.header("Pipeline")
            for status, n in counts.items():
                st.metric(LABELS[status], n)
            st.caption(f"Drafts live in {Path(get_settings().drafts_dir).name}/ inside private/.")

        if msg := st.session_state.pop("flash", None):
            st.toast(msg)

        tabs = st.tabs(
            [
                f"To review ({counts[JobStatus.DRAFTED]})",
                f"Approved ({counts[JobStatus.APPROVED]})",
                f"In progress ({counts[JobStatus.APPLIED] + counts[JobStatus.INTERVIEW]})",
                "History",
            ]
        )
        with tabs[0]:
            review_tab(session)
        with tabs[1]:
            submit_tab(session)
        with tabs[2]:
            track_tab(session)
        with tabs[3]:
            history_tab(session)


main()
