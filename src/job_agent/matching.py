"""Step 4: score each shortlisted job against your master resume (0-100).

score = 65% meaning similarity + 35% skill overlap - 5 points per missing must-have skill

Meaning similarity uses a small local embedding model, so your resume never
leaves your laptop. The first run downloads the model (about 130 MB) once.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from job_agent.config import SearchConfig
from job_agent.filters import has_skill
from job_agent.models import Job
from job_agent.resume import MasterResume

MODEL_NAME = "BAAI/bge-small-en-v1.5"

# This model's cosine similarities sit roughly between these two values for
# "unrelated" and "very close" texts. They're stretched to 0-1. If scores look
# bunched up after your first run, these are the two numbers to tune.
SIM_LOW, SIM_HIGH = 0.45, 0.85

SEMANTIC_WEIGHT, SKILL_WEIGHT = 0.65, 0.35
SKILLS_FOR_FULL_MARKS = 6
MISSING_MUST_HAVE_PENALTY = 5.0


class Embedder(Protocol):
    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


class LocalEmbedder:
    """sentence-transformers model, loaded lazily so other commands stay fast."""

    def __init__(self, model_name: str = MODEL_NAME):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        return self._model.encode(
            list(texts), normalize_embeddings=True, batch_size=32, show_progress_bar=False
        )


@dataclass
class MatchResult:
    score: float
    semantic: float
    matched_skills: list[str] = field(default_factory=list)
    missing_must_haves: list[str] = field(default_factory=list)

    def details(self) -> dict:
        return {
            "semantic": round(self.semantic, 3),
            "matched_skills": self.matched_skills,
            "missing_must_haves": self.missing_must_haves,
        }


def job_text(job: Job) -> str:
    # The title is repeated because Adzuna descriptions are short; it carries the most signal.
    return f"{job.title}. {job.title}. {job.company}. {job.description}"


def _stretch(cos: float) -> float:
    return float(np.clip((cos - SIM_LOW) / (SIM_HIGH - SIM_LOW), 0.0, 1.0))


class Matcher:
    def __init__(self, resume: MasterResume, search: SearchConfig, embedder: Embedder):
        self.resume = resume
        self.search = search
        self.embedder = embedder
        self.skills = resume.skill_names()
        roles_text = "Target roles: " + ", ".join(search.roles)
        self._resume_vec, self._roles_vec = embedder.encode([resume.as_text(), roles_text])

    def score_many(self, jobs: Sequence[Job]) -> list[MatchResult]:
        if not jobs:
            return []
        vecs = self.embedder.encode([job_text(j) for j in jobs])
        return [self._score(j, v) for j, v in zip(jobs, vecs, strict=True)]

    def _score(self, job: Job, vec: np.ndarray) -> MatchResult:
        # Similar to your experience, and similar to the roles you want.
        semantic = 0.5 * _stretch(float(vec @ self._resume_vec)) + 0.5 * _stretch(
            float(vec @ self._roles_vec)
        )
        text = f"{job.title}\n{job.description}"
        matched = [s for s in self.skills if has_skill(text, s)]
        skill_part = min(len(matched), SKILLS_FOR_FULL_MARKS) / SKILLS_FOR_FULL_MARKS
        missing = [s for s in self.search.must_have_skills if not has_skill(text, s)]

        score = 100 * (SEMANTIC_WEIGHT * semantic + SKILL_WEIGHT * skill_part)
        score -= MISSING_MUST_HAVE_PENALTY * len(missing)
        return MatchResult(
            score=round(float(np.clip(score, 0, 100)), 1),
            semantic=semantic,
            matched_skills=matched,
            missing_must_haves=missing,
        )
