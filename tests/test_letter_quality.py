"""The five cover letter fixes, tested against the real first draft for job #454."""

import pytest

from job_agent.models import Job
from job_agent.resume import MasterResume
from job_agent.text_utils import short_company
from job_agent.truthfulness import check_draft

RESUME = MasterResume.model_validate(
    {
        "name": "R",
        "summary": "MSc AI graduate with 2 years of experience.",
        "skills": {"Core": ["Python", "SQL", "dbt", "Snowflake", "AWS S3", "FastAPI", "BERT"]},
        "experience": [
            {
                "title": "AI & Data Intern",
                "company": "YCX.ai",
                "start": "2025-02",
                "bullets": [
                    "Engineered scalable ETL pipelines using AWS S3, dbt, and Snowflake to "
                    "support monthly ML model training cycles improving data read "
                    "performance by 30%.",
                    "Built Python and SQL based reporting workflows that accelerated query "
                    "processing time by 45%.",
                ],
            }
        ],
        "projects": [
            {
                "name": "Fake news",
                "tech": ["BERT", "RoBERTa-MNLI", "FastAPI"],
                "bullets": [
                    "Multi-stage NLP pipeline achieving 91% accuracy, deployed as a "
                    "FastAPI application, with unit testing."
                ],
            }
        ],
    }
)

JOB = Job(
    dedup_key="k",
    title="AI Engineer",
    company="Trigent Software Private Limited",
    apply_url="https://x.com",
    source="adzuna",
)

# The first real draft (job #454), body only.
FIRST_DRAFT = """I am applying for the AI Engineer position at Trigent Software Private Limited. My background includes an MSc in Artificial Intelligence and practical experience developing data pipelines and machine learning workflows. I focus on building reliable backend systems that connect data engineering with AI deployment.

During my internship at YCX.ai, I engineered scalable ETL pipelines using AWS S3, dbt, and Snowflake. This work improved data read performance by 30% and streamlined monthly model training cycles. I also built SQL and Python workflows to accelerate query processing, ensuring data was structured and ready for analytical use.

In my independent project work, I designed a multi-stage NLP pipeline using BERT and RoBERTa-MNLI. I implemented a semantic retrieval layer using the Wikipedia API to fetch and rank evidence at inference time, which mirrors retrieval-augmented generation systems. I deployed this entire pipeline as a FastAPI application, ensuring clean code and thorough unit testing.

While I have not yet worked directly with agentic AI orchestration, I am keen to apply my software engineering discipline and NLP knowledge to these systems. I would welcome the opportunity to discuss how my technical skills can support your team."""  # noqa: E501

BETTER = """Trigent's AI Engineer role centres on connecting data pipelines to deployed models, which is what I spent a year doing at YCX.ai. There I engineered ETL pipelines on AWS S3, dbt and Snowflake that supported monthly ML training cycles and improved data read performance by 30%. I also built Python and SQL reporting workflows that cut query processing time by 45%.

For my fake news detection project I built a multi-stage NLP pipeline with BERT and RoBERTa-MNLI that reached 91% accuracy. It fetches and ranks evidence from the Wikipedia API at inference time, a practical form of retrieval-augmented generation, and I deployed it as a FastAPI application with unit tests.

I have not yet worked with agentic frameworks or tool-calling, and I would be keen to learn them on the job. The retrieval and deployment work above is the closest I have come, and it is the part of the role I would most like to grow into.

I would be glad to talk through the YCX.ai pipelines or the NLP project in more detail if that would help."""  # noqa: E501


def plan(letter):
    return {"summary": "MSc AI graduate with 2 years of experience.", "cover_letter": letter}


def problems(letter):
    return " | ".join(check_draft(plan(letter), RESUME, JOB).problems)


@pytest.mark.parametrize(
    "name, short",
    [
        ("Trigent Software Private Limited", "Trigent"),
        ("Infosys Limited", "Infosys"),
        ("VY SYSTEMS PRIVATE LIMITED", "VY Systems"),
        ("TMUS Global Solutions", "TMUS"),
        ("Johnson & Johnson Innovative Medicine", "Johnson & Johnson Innovative Medicine"),
        ("iitjobs, Inc.", "iitjobs"),
    ],
)
def test_fix1_short_company_name(name, short):
    assert short_company(name) == short


def test_first_draft_now_fails_on_every_issue_found_in_review():
    p = problems(FIRST_DRAFT)
    assert "streamlined" in p  # fix 2: upgraded verb
    assert "stock opening" in p  # fix 4: opener
    assert "stock closing" in p  # fix 4: closer
    assert "ensuring" in p  # fix 5: filler
    assert "clean code" in p  # fix 5: filler
    assert "reliable backend" in p  # fix 5: filler


def test_fix3_needs_two_real_numbers():
    letter = BETTER.replace("by 45%", "a lot").replace("91% accuracy", "high accuracy")
    assert "at least 2" in problems(letter)


def test_better_letter_passes_everything():
    report = check_draft(plan(BETTER), RESUME, JOB)
    assert report.passed, report.problems


def test_years_dont_count_as_metrics():
    letter = BETTER.replace("by 45%", "in 2025").replace("91% accuracy", "high accuracy")
    assert "at least 2" in problems(letter)


# The second real draft (job #454 re-draft), body only.
SECOND_DRAFT = """My experience building semantic retrieval systems and data pipelines in Python aligns closely with Trigent's requirements for the AI Engineer position. During my MSc in Artificial Intelligence and my recent internship, I have focused on connecting machine learning models with structured data sources and deploying them as APIs.

For my context-aware fact verification project, I built a multi-stage NLP pipeline using PyTorch, Hugging Face, and spaCy, which achieved 91% accuracy. This system featured a semantic retrieval layer that queried the Wikipedia API to inject real-time context at inference time, before serving predictions via a FastAPI application.

Additionally, as an AI and Data Intern at YCX.ai, I engineered ETL pipelines using AWS S3, dbt, and Snowflake to support monthly model training. I improved data read performance by 30% and built reporting workflows that accelerated query processing by 45%.

I would welcome the opportunity to discuss how my background in NLP and data engineering matches your current projects."""  # noqa: E501

GAPS = {
    "summary": "",
    "strengths": [],
    "gaps": ["No agentic AI or tool-calling experience", "No vector database experience"],
}


def problems_with_gaps(letter, gaps=GAPS):
    p = {
        "summary": "MSc AI graduate with 2 years of experience.",
        "cover_letter": letter,
        "fit": gaps,
    }
    return " | ".join(check_draft(p, RESUME, JOB).problems)


def test_second_draft_fails_only_on_closer_and_missing_gap():
    p = problems_with_gaps(SECOND_DRAFT).split(" | ")
    assert len(p) == 2, p
    assert "stock closing" in p[0] or "stock closing" in p[1]
    assert any("doesn't name a gap" in x for x in p)


@pytest.mark.parametrize(
    "closer",
    [
        "I would welcome the opportunity to discuss how my background in NLP matches your current projects.",  # noqa: E501
        "I would welcome the chance to talk about how I could help your data team.",
        "I'd love to discuss how my skills align with your team's goals.",
        "Let me know how my experience could benefit your organisation.",
    ],
)
def test_generic_closers_caught(closer):
    assert "stock closing" in problems(BETTER.rsplit("\n\n", 1)[0] + "\n\n" + closer)


def test_specific_closer_passes():
    assert "stock closing" not in problems(BETTER)  # "glad to talk through the YCX.ai pipelines"


def test_gap_admission_required_only_when_gaps_exist():
    no_gap_line = BETTER.replace(
        "I have not yet worked with agentic frameworks or tool-calling, and I would be keen to "
        "learn them on the job.",
        "",
    )
    assert "doesn't name a gap" in problems_with_gaps(no_gap_line)
    assert "doesn't name a gap" not in problems_with_gaps(no_gap_line, gaps={"gaps": []})
    assert "doesn't name a gap" not in problems_with_gaps(BETTER)
