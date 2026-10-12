"""The truthfulness guardrail: every claim in a draft must trace back to the master resume.

These checks are plain rules, not another LLM, so they're predictable and free.
They catch the common ways models make things up: numbers, tools, credentials and
seniority. Anything flagged sends the draft back for a rewrite.
"""

import re
from dataclasses import dataclass, field

from job_agent.filters import has_skill
from job_agent.models import Job
from job_agent.resume import MasterResume

# Tools and terms a model might claim. Only terms NOT in your resume are checked.
TECH_TERMS = [
    "Java",
    "Scala",
    "Golang",
    "Rust",
    "Kotlin",
    "PHP",
    "Ruby",
    "React",
    "Angular",
    "Vue",
    "Node.js",
    "Django",
    "Flask",
    "Spring",
    "Next.js",
    "Spark",
    "PySpark",
    "Hadoop",
    "Hive",
    "Kafka",
    "Flink",
    "Beam",
    "Presto",
    "Trino",
    "BigQuery",
    "Redshift",
    "Synapse",
    "Fabric",
    "Informatica",
    "Talend",
    "SSIS",
    "SSRS",
    "Ab Initio",
    "Abinitio",
    "AWS Glue",
    "EMR",
    "Kinesis",
    "SageMaker",
    "Vertex AI",
    "GCP",
    "Google Cloud",
    "Kubernetes",
    "Kubeflow",
    "MLflow",
    "Terraform",
    "Ansible",
    "Jenkins",
    "TensorFlow",
    "Keras",
    "JAX",
    "LangChain",
    "LlamaIndex",
    "OpenCV",
    "YOLO",
    "Pinecone",
    "Weaviate",
    "Chroma",
    "FAISS",
    "Elasticsearch",
    "MongoDB",
    "Cassandra",
    "Redis",
    "Neo4j",
    "Oracle",
    "SQL Server",
    "MySQL",
    "Teradata",
    "Looker",
    "Qlik",
    "SAS",
    "SPSS",
    "MATLAB",
    "Salesforce",
    "SAP",
    "ServiceNow",
    "Jira",
    "Microsoft Fabric",
    "Power Apps",
    "Copilot",
    "computer vision",
    "reinforcement learning",
    "time series",
    "recommendation systems",
]

CREDENTIALS = re.compile(r"\b(PhD|Ph\.D|doctorate|MBA|certified|certification|chartered)\b", re.I)
SENIORITY_CLAIMS = re.compile(
    r"\b(led|managed|supervised|headed|mentored)\s+(a|the|my|an?)?\s*(team|group|squad|engineers)\b"
    r"|\b(team lead|tech lead|senior engineer|line manager)\b",
    re.I,
)
# A sentence about the employer or about learning may mention tools you don't have.
SAFE_CONTEXT = re.compile(
    r"\b(you|your|you're|learn|learning|keen to|eager to|pick up|new to|not yet|would like to|"
    r"look forward|hope to|interested in)\b",
    re.I,
)
CLICHES = [
    "passionate",
    "thrilled",
    "excited to apply",
    "leverage",
    "synergy",
    "cutting-edge",
    "results-driven",
    "i am writing to express",
    "proven track record",
    "dynamic team",
]

# Standalone numbers only, so names like "S3" or "EC2" aren't treated as figures.
NUMBER = re.compile(r"(?<![A-Za-z\d.])\d[\d,]*(?:\.\d+)?")
YEAR = re.compile(r"^(19|20)\d\d$")

# Verbs that inflate what was done. Flagged only when your resume doesn't use them.
UPGRADE_VERBS = [
    "streamline",
    "spearhead",
    "drive",
    "drove",
    "transform",
    "revolutionise",
    "revolutionize",
    "pioneer",
    "champion",
    "orchestrate",
    "owned",
    "overhaul",
    "supercharge",
]
# Qualities a model likes to add. Flagged only when your resume doesn't say them.
FILLER = [
    "clean code",
    "best practices",
    "robust",
    "seamless",
    "seamlessly",
    "high-quality",
    "world-class",
    "detail-oriented",
    "team player",
    "fast learner",
    "ensuring",
    "hit the ground running",
    "reliable backend",
]
STOCK_OPENERS = re.compile(
    r"^\s*(i am (writing|applying)|i'm (writing|applying)|i would like to apply|"
    r"please accept|i wish to apply)",
    re.I,
)
STOCK_CLOSERS = re.compile(
    # "how my background matches your current projects", "how I could help your team" ...
    r"how (my|i)\b[^.]{0,60}?\b(support|benefit|contribute|add value|match(es)?|fit(s)?|"
    r"align(s)?|help)\b[^.]{0,40}?\b(team|projects?|needs|requirements|initiatives|goals|"
    r"organi[sz]ation|company|business)\b"
    r"|valuable addition to your team"
    r"|look forward to hearing from you at your earliest convenience",
    re.I,
)
# An honest, specific admission of a gap, e.g. "I have not yet used vector databases".
GAP_ADMISSION = re.compile(
    r"\b(not yet|haven't yet|have not yet|haven't (used|worked)|have not (used|worked)|"
    r"no (direct|hands-on|professional) experience|new to|keen to learn|eager to learn|"
    r"would like to learn|looking forward to learning)\b",
    re.I,
)


@dataclass
class TruthReport:
    problems: list[str] = field(default_factory=list)  # must be fixed
    warnings: list[str] = field(default_factory=list)  # style only

    @property
    def passed(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict:
        return {"passed": self.passed, "problems": self.problems, "warnings": self.warnings}


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUMBER.findall(text)}


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def check_text(label: str, text: str, resume: MasterResume, job: Job, report: TruthReport) -> None:
    resume_text = resume.as_text() + "\n" + " ".join(resume.skill_names())
    allowed_numbers = _numbers(resume_text) | _numbers(f"{job.title} {job.company}")

    for n in sorted(_numbers(text) - allowed_numbers):
        report.problems.append(f"{label}: the number '{n}' is not in your master resume")

    for sentence in _sentences(text):
        for term in TECH_TERMS:
            if has_skill(job.company, term):
                continue  # naming the employer (e.g. SAP, Oracle) is not a skill claim
            if has_skill(sentence, term) and not has_skill(resume_text, term):
                if not SAFE_CONTEXT.search(sentence):
                    report.problems.append(
                        f"{label}: claims '{term}', which is not in your master resume "
                        f'(in: "{sentence.strip()[:120]}")'
                    )
        if (m := CREDENTIALS.search(sentence)) and not re.search(
            re.escape(m.group(0)), resume_text, re.I
        ):
            report.problems.append(f"{label}: mentions '{m.group(0)}', which is not in your resume")
        if m := SENIORITY_CLAIMS.search(sentence):
            report.problems.append(
                f"{label}: claims '{m.group(0)}', which your resume doesn't support"
            )

    lower = text.lower()
    resume_lower = resume_text.lower()
    for verb in UPGRADE_VERBS:
        pattern = rf"\b{verb}(s|d|ed|ing|ership)?\b" if verb not in ("drove",) else r"\bdrove\b"
        if re.search(pattern, lower) and not re.search(pattern, resume_lower):
            word = re.search(pattern, lower).group(0)
            report.problems.append(f"{label}: '{word}' overstates your resume; keep its own verbs")
    for phrase in FILLER:
        if re.search(rf"\b{re.escape(phrase)}\b", lower) and phrase not in resume_lower:
            report.problems.append(f"{label}: '{phrase}' adds a claim your resume doesn't make")
    for c in CLICHES:
        if c in lower:
            report.warnings.append(f"{label}: uses the cliché '{c}'")
    if "\u2014" in text:
        report.warnings.append(f"{label}: uses em dashes")


def check_draft(draft: dict, resume: MasterResume, job: Job) -> TruthReport:
    report = TruthReport()
    summary = draft.get("summary", "")
    letter = draft.get("cover_letter", "")
    check_text("summary", summary, resume, job, report)
    check_text("cover letter", letter, resume, job, report)
    if len(summary.split()) > 45:
        report.problems.append(
            f"summary: {len(summary.split())} words; keep it to 2 lines (max 40)"
        )
    words = len(letter.split())
    if not 140 <= words <= 300:
        report.problems.append(f"cover letter: {words} words; aim for 170 to 240")
    body = re.sub(r"^\s*(dear|hi|hello)\b[^\n]*\n+", "", letter, flags=re.I)
    if STOCK_OPENERS.search(body):
        report.problems.append(
            "cover letter: stock opening line; open with a specific link to the job"
        )
    if m := STOCK_CLOSERS.search(letter):
        report.problems.append(f"cover letter: stock closing ('{m.group(0)}')")
    gaps = (draft.get("fit") or {}).get("gaps") or []
    if gaps and not GAP_ADMISSION.search(letter):
        report.problems.append(
            "cover letter: doesn't name a gap honestly; mention one of these briefly and say "
            "you're keen to learn it: " + "; ".join(gaps[:3])
        )
    resume_numbers = _numbers(resume.as_text())
    used = {n for n in _numbers(letter) & resume_numbers if not YEAR.match(n)}
    if len(used) < 2:
        report.problems.append(
            f"cover letter: uses {len(used)} real number(s) from your resume; include at least 2"
        )
    if re.match(r"\s*(dear|hi|hello)\b", letter, re.I):
        report.warnings.append("cover letter: included its own greeting (it will be removed)")
    return report
