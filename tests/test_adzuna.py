import httpx
import pytest

from job_agent.config import SearchConfig
from job_agent.ingest import ingest
from job_agent.sources.adzuna import AdzunaSource
from job_agent.sources.base import PoliteClient, RequestBudgetExceeded, SourceConfigError


def fake_result(i=1, **overrides):
    raw = {
        "id": str(1000 + i),
        "title": f"Machine Learning Engineer {i}",
        "company": {"display_name": "Acme AI"},
        "location": {
            "display_name": "Bengaluru, Karnataka",
            "area": ["India", "Karnataka", "Bangalore"],
        },
        "description": "We need 0-2 years of Python and SQL experience.",
        "redirect_url": f"https://www.adzuna.in/details/{1000 + i}",
        "created": "2026-10-01T09:30:00Z",
        "salary_min": 800000,
        "salary_max": 1200000,
        "salary_is_predicted": "0",
    }
    raw.update(overrides)
    return raw


def search(**kw):
    base = {"roles": ["ML Engineer"], "locations": ["Bengaluru"], "include_remote": False}
    return SearchConfig(**{**base, **kw})


def make_source(handler, **kw):
    client = PoliteClient(
        min_interval=0,
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
        max_requests=kw.pop("max_requests", 100),
    )
    return AdzunaSource(client=client, app_id="test-id", app_key="test-key", **kw)


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    monkeypatch.setattr(PoliteClient.get_json.retry, "sleep", lambda s: None)


def test_maps_result_to_posting():
    src = make_source(lambda req: httpx.Response(200, json={"results": [fake_result()]}))
    [job] = list(src.fetch(search()))
    assert job.company == "Acme AI"
    assert job.city == "Bengaluru"  # "Bangalore" normalised
    assert job.salary_min_inr == 800000
    assert (job.experience_min_years, job.experience_max_years) == (0, 2)
    assert job.source == "adzuna" and job.source_job_id == "1001"


def test_predicted_salary_is_dropped():
    raw = fake_result(salary_is_predicted="1")
    src = make_source(lambda req: httpx.Response(200, json={"results": [raw]}))
    [job] = list(src.fetch(search()))
    assert job.salary_min_inr is None and job.salary_max_inr is None


def test_missing_company_is_skipped():
    raw = fake_result(company={})
    src = make_source(lambda req: httpx.Response(200, json={"results": [raw]}))
    assert list(src.fetch(search())) == []
    assert src.skipped == 1


def test_sends_india_endpoint_and_params():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"results": []})

    list(make_source(handler).fetch(search(min_salary_inr=600000)))
    url = seen[0].url
    assert url.path == "/v1/api/jobs/in/search/1"
    assert url.params["what"] == "ML Engineer" and url.params["where"] == "Bengaluru"
    assert url.params["salary_min"] == "600000"
    assert url.params["max_days_old"] == "7"


def test_queries_cover_roles_cities_and_remote():
    src = make_source(lambda r: httpx.Response(200, json={"results": []}))
    qs = src.build_queries(
        search(roles=["A", "B"], locations=["Pune", "Mumbai"], include_remote=True)
    )
    assert len(qs) == 6 and ("A remote", None) in qs


def test_retries_after_429_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"results": [fake_result()]})

    assert len(list(make_source(handler).fetch(search()))) == 1
    assert calls["n"] == 2


def test_bad_keys_give_clear_error():
    src = make_source(lambda r: httpx.Response(401))
    with pytest.raises(SourceConfigError, match="rejected your keys"):
        list(src.fetch(search()))


def test_missing_keys_give_clear_error(monkeypatch):
    from job_agent import config

    monkeypatch.setattr(config.get_settings(), "adzuna_app_id", None)
    with pytest.raises(SourceConfigError, match="keys are missing"):
        AdzunaSource()


def test_request_budget_stops_run():
    src = make_source(lambda r: httpx.Response(200, json={"results": []}), max_requests=2)
    list(src.fetch(search(roles=["A", "B", "C"])))
    assert src.client.requests_made == 2
    with pytest.raises(RequestBudgetExceeded):
        src.client.get_json("https://api.adzuna.com/x")


def test_rate_limit_waits_between_requests():
    waits = []
    client = PoliteClient(
        min_interval=3.0,
        sleep=waits.append,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": []})),
    )
    src = AdzunaSource(client=client, app_id="i", app_key="k")
    list(src.fetch(search(roles=["A", "B"])))
    assert len(waits) == 1 and 2.5 < waits[0] <= 3.0


def test_ingest_saves_and_dedupes(session):
    results = [fake_result(1), fake_result(2), fake_result(1)]  # third is a repeat
    src = make_source(lambda r: httpx.Response(200, json={"results": results}))
    stats = ingest(src, search(), session)
    assert (stats.fetched, stats.new, stats.duplicates) == (3, 2, 1)
