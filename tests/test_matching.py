import json
import re
from pathlib import Path

import numpy as np
import pytest

from job_agent.config import SearchConfig
from job_agent.db import change_status, upsert_job
from job_agent.lifecycle import Actor, JobStatus
from job_agent.matching import Matcher
from job_agent.ranking import match_jobs
from job_agent.resume import MasterResume, load_resume
from job_agent.schemas import JobPosting

RESUME = {
    "name": "Test Person",
    "contact": {"email": "test@example.com"},
    "summary": "AI graduate building machine learning, NLP and data pipelines in Python and SQL.",
    "skills": {
        "Programming": ["Python (Pandas, NumPy, OOP)", "SQL (Advanced)", "R"],
        "AI": ["PyTorch", "NLP", "LLMs"],
        "Cloud": ["AWS (S3, Lambda)", "Terraform (Awareness)"],
    },
    "experience": [
        {
            "title": "AI Intern",
            "company": "X",
            "start": "2025-02",
            "bullets": ["Built machine learning data pipelines with Python and SQL."],
        }
    ],
    "projects": [
        {
            "name": "Fake news",
            "tech": ["FastAPI", "BERT"],
            "bullets": ["NLP pipeline with BERT and PyTorch."],
        }
    ],
}


class BagOfWordsEmbedder:
    """Tiny deterministic stand-in for the real model: shared words => similar vectors."""

    def encode(self, texts):
        vecs = []
        for t in texts:
            v = np.zeros(512)
            for w in re.findall(r"[a-z]+", t.lower()):
                v[hash(w) % 512] += 1
            n = np.linalg.norm(v)
            vecs.append(v / n if n else v)
        return np.array(vecs)


@pytest.fixture
def resume():
    return MasterResume.model_validate(RESUME)


@pytest.fixture
def search():
    return SearchConfig(
        roles=["Machine Learning Engineer", "Data Scientist"],
        locations=["Pune"],
        must_have_skills=["Python", "SQL"],
        min_match_score=30,
    )


def test_skill_names_split_brackets(resume):
    names = resume.skill_names()
    assert {"Python", "Pandas", "NumPy", "OOP", "SQL", "AWS", "S3", "Lambda", "Terraform"} <= set(
        names
    )
    assert "Advanced" not in names and "Awareness" not in names
    assert "R" not in names  # one-letter skills are skipped
    assert "FastAPI" in names  # project tech is included


def test_resume_text_has_no_contact_details(resume):
    assert "test@example.com" not in resume.as_text()


def test_real_master_resume_loads():
    path = Path(__file__).resolve().parents[1] / "private" / "master_resume.json"
    if not path.exists():
        pytest.skip("no private/master_resume.json on this machine")
    r = load_resume(path)
    assert r.experience and r.projects and "Python" in r.skill_names()


def _add(session, title, desc):
    p = JobPosting(
        title=title,
        company="Co",
        city="Pune",
        description=desc,
        apply_url="https://co.com/j",
        source="greenhouse",
    )
    job, _ = upsert_job(session, p)
    change_status(session, job, JobStatus.SHORTLISTED, Actor.AGENT)
    return job


def test_relevant_job_beats_irrelevant(session, resume, search):
    ml = _add(
        session,
        "Machine Learning Engineer",
        "Build machine learning and NLP models in Python, PyTorch and SQL on AWS.",
    )
    med = _add(
        session,
        "Medical Science Liaison, Oncology",
        "Engage oncologists, present clinical trial data, travel across the region.",
    )
    m = Matcher(resume, search, BagOfWordsEmbedder())
    r_ml, r_med = m.score_many([ml, med])
    assert r_ml.score > r_med.score + 20
    assert {"Python", "PyTorch", "SQL", "AWS", "NLP"} <= set(r_ml.matched_skills)
    assert r_med.missing_must_haves == ["Python", "SQL"]


def test_low_match_moves_to_filtered_out_with_reason(session, resume, search):
    ml = _add(
        session, "Machine Learning Engineer", "Machine learning in Python, SQL, PyTorch, NLP."
    )
    med = _add(session, "Medical Science Liaison", "Clinical oncology field role.")
    stats = match_jobs(session, search, Matcher(resume, search, BagOfWordsEmbedder()))
    assert stats.scored == 2 and stats.filtered_out == 1
    assert ml.status == JobStatus.SHORTLISTED and ml.match_score is not None
    assert med.status == JobStatus.FILTERED_OUT and med.notes.startswith("low match")
    assert "matched_skills" in ml.match_details


def test_already_scored_jobs_are_skipped(session, resume, search):
    _add(session, "Machine Learning Engineer", "Python SQL machine learning")
    matcher = Matcher(resume, search, BagOfWordsEmbedder())
    match_jobs(session, search, matcher)
    assert match_jobs(session, search, matcher).scored == 0


def test_rescore_restores_low_match_after_lowering_cutoff(session, resume, search):
    med = _add(session, "Medical Science Liaison", "Clinical oncology field role.")
    matcher = Matcher(resume, search, BagOfWordsEmbedder())
    match_jobs(session, search, matcher)
    assert med.status == JobStatus.FILTERED_OUT
    search.min_match_score = 0
    stats = match_jobs(session, search, Matcher(resume, search, BagOfWordsEmbedder()), rescore=True)
    assert stats.restored == 1 and med.status == JobStatus.SHORTLISTED


def test_rescore_never_touches_jobs_filtered_for_other_reasons(session, resume, search):
    p = JobPosting(
        title="Senior ML Engineer",
        company="Co",
        city="Pune",
        apply_url="https://co.com/s",
        source="greenhouse",
    )
    job, _ = upsert_job(session, p)
    change_status(session, job, JobStatus.FILTERED_OUT, Actor.AGENT, reason="seniority")
    job.notes = "seniority: 'Senior' in title"
    search.min_match_score = 0
    stats = match_jobs(session, search, Matcher(resume, search, BagOfWordsEmbedder()), rescore=True)
    assert stats.scored == 0 and job.status == JobStatus.FILTERED_OUT


def test_dry_run_saves_nothing(session, resume, search):
    med = _add(session, "Medical Science Liaison", "Clinical oncology field role.")
    stats = match_jobs(session, search, Matcher(resume, search, BagOfWordsEmbedder()), dry_run=True)
    assert stats.filtered_out == 1
    assert med.status == JobStatus.SHORTLISTED and med.match_score is None
    assert "Score spread" in stats.report(search.min_match_score)


def test_resume_json_is_valid_shape(tmp_path):
    f = tmp_path / "r.json"
    f.write_text(json.dumps(RESUME))
    assert load_resume(f).name == "Test Person"
    with pytest.raises(FileNotFoundError, match="private/ folder"):
        load_resume(tmp_path / "missing.json")
