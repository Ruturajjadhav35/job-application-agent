"""Step 3: decide whether each new job is worth shortlisting.

Every check returns plain-English reasons, so you can always see why a job
was filtered out. Nothing here uses an LLM; it's all simple, predictable rules.
"""

import re
from dataclasses import dataclass, field

from job_agent.config import SearchConfig
from job_agent.models import Job
from job_agent.text_utils import normalise_city

# Sources that only give a shortened description. Missing skills can't be
# trusted there, so they lower the ranking later instead of filtering.
SHORT_DESCRIPTION_SOURCES = {"adzuna"}

# ---------- scam signals ----------
SCAM_PATTERNS: dict[str, re.Pattern] = {
    "asks for a fee or deposit": re.compile(
        r"\b(registration|security|training|processing|joining|application|refundable)"
        r"\s+(fee|fees|deposit|charges?)\b|\bpay\s+(rs\.?|inr|₹)\s*\d",
        re.I,
    ),
    "contact only via WhatsApp/Telegram": re.compile(
        r"\b(whatsapp|telegram)\b.{0,40}\b(contact|message|ping|reach|apply|dm)\b"
        r"|\b(contact|message|ping|reach|apply|dm)\b.{0,40}\b(whatsapp|telegram)\b",
        re.I,
    ),
    "task-based earning scheme": re.compile(
        r"\b(earn|income|payout)\b.{0,30}\b(per task|per day|daily|per like|per review)\b"
        r"|\b(like|rate|review)\s+(videos|products|hotels|apps)\b",
        re.I,
    ),
    "uses a free email address": re.compile(r"[\w.+-]+@(gmail|yahoo|outlook|hotmail)\.com", re.I),
    "no interview / instant offer": re.compile(
        r"\b(no interview|without interview|instant (joining|offer)|100% job guarantee)\b", re.I
    ),
}

# ---------- seniority ----------
SENIOR_TITLE = re.compile(
    r"\b(senior|sr\.?|lead|principal|director|manager|mgr|vp|avp|vice president|head|"
    r"architect|chief|distinguished|expert)\b|(?<!technical )\bstaff\b",
    re.I,
)
LEVEL_TITLE = re.compile(
    # Roman numerals are matched in capitals only, so "i"/"ii" inside words don't count.
    r"\b(II|III|IV)\b|(?i:\b(sde|engineer|scientist|analyst|developer)[\s-]*(2|3|ii|iii)\b)"
    r"|(?i:\blevel\s*[3-9]\b)|\bL[4-9]\b",
)

# ---------- clearly off-target roles ----------
OFF_TARGET_TITLE = re.compile(
    r"\b(recruiter|talent acquisition|sales|marketing|accountant|human resources|hr "
    r"(executive|generalist)|customer (support|service)|business development|telecaller)\b",
    re.I,
)

SKILL_SYNONYMS = {
    "python": ["python", "pyspark", "pandas", "django", "flask", "fastapi"],
    "sql": ["sql", "mysql", "postgresql", "postgres", "t-sql", "pl/sql", "sqlite"],
}


@dataclass
class ScreenResult:
    shortlist: bool
    reasons: list[str] = field(default_factory=list)  # why it was filtered out
    scam_flags: list[str] = field(default_factory=list)
    unconfirmed_skills: list[str] = field(default_factory=list)  # short descriptions only

    def summary(self) -> str:
        if not self.shortlist:
            return "; ".join(self.reasons)
        if self.unconfirmed_skills:
            return "passed checks; couldn't confirm " + ", ".join(self.unconfirmed_skills)
        return "passed checks"


def _has_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}\b", text, re.I) is not None


def has_skill(text: str, skill: str) -> bool:
    words = SKILL_SYNONYMS.get(skill.lower(), [skill])
    return any(_has_phrase(text, w) for w in words)


def screen(job: Job, search: SearchConfig) -> ScreenResult:
    text = f"{job.title}\n{job.description}"
    result = ScreenResult(shortlist=True)

    # 1. Scam signals
    result.scam_flags = [name for name, pat in SCAM_PATTERNS.items() if pat.search(text)]
    if result.scam_flags:
        result.reasons.append("scam: " + ", ".join(result.scam_flags))

    # 2. Your deal-breakers
    hits = [p for p in search.deal_breakers if _has_phrase(text, p)]
    if hits:
        result.reasons.append("deal-breaker: " + ", ".join(hits))

    # 3. Clearly off-target roles
    if m := OFF_TARGET_TITLE.search(job.title):
        result.reasons.append(f"off-target role: {m.group(0).lower()}")

    # 4. Seniority from the title
    if m := SENIOR_TITLE.search(job.title):
        result.reasons.append(f"seniority: '{m.group(0)}' in title")
    elif m := LEVEL_TITLE.search(job.title):
        result.reasons.append(f"seniority: level '{m.group(0)}' in title")

    # 5. Experience (unknown experience is kept)
    if job.experience_min_years is not None and (
        job.experience_min_years > search.max_experience_years
    ):
        result.reasons.append(f"experience: asks for {job.experience_min_years}+ years")

    # 6. Location (remote is fine if you allow it; unknown city is kept)
    wanted = {normalise_city(c) for c in search.locations}
    remote_ok = job.is_remote and search.include_remote
    if not remote_ok and job.city and normalise_city(job.city) not in wanted:
        result.reasons.append(f"location: {job.city}")

    # 7. Must-have skills
    missing = [s for s in search.must_have_skills if not has_skill(text, s)]
    if missing:
        if job.source in SHORT_DESCRIPTION_SOURCES:
            result.unconfirmed_skills = missing
        else:
            result.reasons.append("missing skills: " + ", ".join(missing))

    result.shortlist = not result.reasons
    return result
