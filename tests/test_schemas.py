import pytest
from pydantic import ValidationError

from job_agent.schemas import JobPosting, make_dedup_key


def test_text_is_stripped(posting_data):
    assert JobPosting(**posting_data).title == "Machine Learning Engineer"


def test_same_job_on_different_sites_gets_same_key():
    a = make_dedup_key("Acme Technologies Pvt Ltd", "ML Engineer", "Bengaluru")
    b = make_dedup_key("ACME", "ML engineer", "bengaluru")
    assert a == b


def test_different_city_gets_different_key():
    assert make_dedup_key("Acme", "ML Engineer", "Pune") != make_dedup_key(
        "Acme", "ML Engineer", "Bengaluru"
    )


def test_bad_salary_range_rejected(posting_data):
    posting_data.update(salary_min_inr=2_000_000, salary_max_inr=1_000_000)
    with pytest.raises(ValidationError):
        JobPosting(**posting_data)


def test_bad_url_rejected(posting_data):
    posting_data["apply_url"] = "not a url"
    with pytest.raises(ValidationError):
        JobPosting(**posting_data)


def test_company_name_variants_match():
    k = make_dedup_key
    assert k("Athena Health", "ML Engineer", "Bengaluru") == k(
        "athenahealth", "ML Engineer", "Bengaluru"
    )
    assert k("Danaher Corporation", "Lead AI", "Pune") == k("Danaher", "Lead AI", "Pune")
    assert k("Circana Careers", "AI Context Engineer", "Bangalore") == k(
        "Circana", "AI Context Engineer", "Bengaluru"
    )
    assert k("Amazon", "Applied Scientist I", "Bengaluru") != k(
        "Amazon", "Applied Scientist II", "Bengaluru"
    )
