import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

import job_agent.models  # noqa: F401


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def posting_data():
    return {
        "title": "  Machine Learning Engineer ",
        "company": "Acme Technologies Pvt Ltd",
        "city": "Bengaluru",
        "apply_url": "https://boards.greenhouse.io/acme/jobs/123",
        "source": "greenhouse",
        "salary_min_inr": 1_000_000,
        "salary_max_inr": 1_600_000,
    }
