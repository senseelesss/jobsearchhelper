import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from job_radar.cli import _scrape, parser
from job_radar.http import HttpResponse
from job_radar.sources.karriere import collect, parse_search, search_url
from job_radar.sources.browser import PlaywrightClient

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.karriere.at/jobs/python/wien"


class FakeClient:
    def __init__(self, body, status=200, final_url=URL):
        self.response = HttpResponse(status, body, final_url)
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        return self.response


def test_search_extracts_native_ids_and_preserves_missing_fields():
    result = parse_search(
        (FIXTURES / "karriere_search.html").read_text(), URL,
        observed_at="2026-11-04T10:00:00+00:00",
    )
    assert result.status == "success"
    assert len(result.jobs) == 2
    assert result.cards_seen == 3
    first, second = result.jobs
    assert first.dedupe_key == "karriere:1234567"
    assert first.location == "Wien, Graz"
    assert first.url == "https://www.karriere.at/jobs/1234567"
    assert second.company == "Sample & Co"
    assert second.snippet == ""
    assert first.observed_at == "2026-11-04T10:00:00+00:00"
    assert "email_message_id" not in first.to_dict()
    assert first.description_kind == "snippet"
    assert result.coverage_complete is False


@pytest.mark.parametrize(("body", "status", "expected"), [
    ("<h1>0 Python Jobs Wien</h1>", 200, "empty"),
    ("<h1>10 Python Jobs Wien</h1>", 200, "failed"),
    ("<h1>Welcome</h1>", 200, "failed"),
    ("<h1>Verify you are human</h1>", 200, "blocked"),
    ("", 403, "blocked"), ("", 429, "rate_limited"), ("", 503, "failed"),
])
def test_empty_blocked_and_broken_layout_are_distinct(body, status, expected):
    assert parse_search(body, URL, status_code=status).status == expected


def test_invalid_card_keeps_valid_jobs_and_reports_partial():
    body = (FIXTURES / "karriere_search.html").read_text().replace(
        'href="/jobs/7654321"', 'href="https://evil.example/jobs/7654321"'
    )
    result = parse_search(body, URL)
    assert result.status == "partial"
    assert result.invalid_cards == 1
    assert len(result.jobs) == 1


def test_collection_is_one_bounded_request_without_email():
    client = FakeClient((FIXTURES / "karriere_search.html").read_text())
    result = collect(client, "python", "wien", limit=1)
    assert len(result.jobs) == 1
    assert client.calls == [URL]
    assert result.reported_total == 3


def test_redirect_and_transport_failures_are_reported_without_details():
    client = FakeClient("", final_url="https://example.com/login")
    assert collect(client, "python", "wien").status == "blocked"

    class BrokenClient:
        def get(self, url):
            raise TimeoutError("private session detail")

    result = collect(BrokenClient(), "python", "wien")
    assert result.status == "failed"
    assert "private session" not in result.error


def test_search_query_cannot_inject_a_path():
    assert search_url("Python Developer", "Wien") == "https://www.karriere.at/jobs/python-developer/wien"
    assert "%2F" in search_url("python/admin", "wien")
    with pytest.raises(ValueError):
        search_url("", "wien")


def test_scrape_cli_exports_without_database_or_gmail(monkeypatch, tmp_path):
    client = FakeClient((FIXTURES / "karriere_search.html").read_text())
    monkeypatch.setattr("job_radar.http.HttpClient", lambda **kwargs: client)
    output = tmp_path / "jobs.json"
    args = parser().parse_args([
        "scrape", "--transport", "http", "--limit", "1", "--output", str(output),
    ])
    _scrape(args)
    data = json.loads(output.read_text())
    assert data["jobs"][0]["source_job_id"] == "1234567"
    assert not list(tmp_path.glob("*.db"))


def test_scrape_failure_has_nonzero_exit_and_saved_diagnostics(monkeypatch, tmp_path):
    monkeypatch.setattr("job_radar.http.HttpClient", lambda **kwargs: FakeClient("", 429))
    output = tmp_path / "failed.json"
    args = parser().parse_args(["scrape", "--transport", "http", "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        _scrape(args)
    assert error.value.code == 1
    assert json.loads(output.read_text())["status"] == "rate_limited"


@pytest.mark.parametrize("fail_navigation", [False, True])
def test_browser_is_closed_even_when_navigation_fails(monkeypatch, fail_navigation):
    closed = []
    calls = []

    def navigate(url, **kwargs):
        calls.append((url, kwargs))
        if fail_navigation:
            raise TimeoutError("navigation failed")
        return SimpleNamespace(status=200)

    page = SimpleNamespace(
        goto=navigate, content=lambda: "<h1>0 Python Jobs Wien</h1>",
        url=URL, set_default_timeout=lambda timeout: None,
    )
    browser = SimpleNamespace(
        new_context=lambda **kwargs: SimpleNamespace(new_page=lambda: page),
        close=lambda: closed.append(True),
    )
    package = ModuleType("playwright")
    api = ModuleType("playwright.sync_api")
    api.sync_playwright = lambda: nullcontext(SimpleNamespace(
        chromium=SimpleNamespace(launch=lambda **kwargs: browser),
    ))
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", api)
    client = PlaywrightClient(timeout_seconds=3)
    if fail_navigation:
        with pytest.raises(TimeoutError):
            client.get(URL)
    else:
        assert client.get(URL).status_code == 200
    assert closed == [True]
    assert calls[0][1]["timeout"] == 3000
