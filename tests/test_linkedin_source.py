import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from job_radar.cli import _scrape, parser
from job_radar.http import HttpResponse
from job_radar.models import EmailMessage
from job_radar.parsers.linkedin import parse_linkedin
from job_radar.sources.linkedin import collect, parse_search, search_url

FIXTURES = Path(__file__).parent / "fixtures"
URL = search_url("python", "Vienna, Austria")


class FakeClient:
    def __init__(self, body, status=200, final_url=URL):
        self.response = HttpResponse(status, body, final_url)
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        return self.response


def test_public_cards_share_identity_with_existing_email_parser():
    result = parse_search(
        (FIXTURES / "linkedin_search.html").read_text(), URL,
        observed_at="2026-11-04T12:00:00+00:00",
    )
    assert result.status == "success"
    assert len(result.jobs) == 2
    assert result.cards_seen == 3
    first, missing = result.jobs
    assert first.company == "Example & Co"
    assert first.location == "Wien, Österreich"
    assert first.published_at == "2026-11-03"
    assert first.url == "https://www.linkedin.com/jobs/view/4428938286/"
    assert missing.company == missing.location == missing.published_at == ""
    assert first.snippet == ""
    assert first.description_kind == "missing"
    assert result.reported_total == 1000
    assert result.reported_total_is_lower_bound
    assert not result.coverage_complete
    email = EmailMessage("synthetic", "", "", "Jobs", "", (FIXTURES / "linkedin.html").read_text())
    assert first.dedupe_key == parse_linkedin(email)[0].dedupe_key
    assert first.url == parse_linkedin(email)[0].url
    assert "email_message_id" not in first.to_dict()


@pytest.mark.parametrize(("html", "status", "expected"), [
    ('<span class="results-context-header__job-count">0</span>', 200, "empty"),
    ('<section class="jobs-search-no-results-banner">No matching jobs found</section>', 200, "empty"),
    ('<span class="results-context-header__job-count">10</span>', 200, "failed"),
    ('<form class="login__form">Sign in</form>', 200, "blocked"),
    ("<h1>Security verification</h1>", 200, "blocked"),
    ("<h1>Welcome</h1>", 200, "failed"),
    ("", 403, "blocked"), ("", 999, "blocked"),
    ("", 429, "rate_limited"), ("", 503, "failed"),
])
def test_outcomes_are_explicit(html, status, expected):
    assert parse_search(html, URL, status_code=status).status == expected


@pytest.mark.parametrize("replacement", [
    "https://evil.example/jobs/view/1234567890/",
    "https://linkedin.com.evil.example/jobs/view/1234567890/",
    "https://www.linkedin.com/company/1234567890/",
])
def test_untrusted_or_nonjob_links_do_not_drop_valid_cards(replacement):
    html = (FIXTURES / "linkedin_search.html").read_text().replace(
        'href="/jobs/view/1234567890/"', f'href="{replacement}"'
    )
    result = parse_search(html, URL)
    assert result.status == "partial"
    assert result.invalid_cards == 1
    assert len(result.jobs) == 1


def test_disagreeing_urn_and_link_is_not_a_valid_native_identity():
    html = (FIXTURES / "linkedin_search.html").read_text().replace(
        "urn:li:jobPosting:4428938286", "urn:li:jobPosting:9999999999"
    )
    result = parse_search(html, URL)
    assert result.status == "partial"
    assert result.invalid_cards == 2
    assert result.jobs[0].source_job_id == "1234567890"


def test_one_request_and_limit_without_gmail_or_pagination():
    client = FakeClient((FIXTURES / "linkedin_search.html").read_text())
    result = collect(client, "python", "Vienna, Austria", limit=1)
    assert client.calls == [URL]
    assert len(result.jobs) == 1


@pytest.mark.parametrize("target", [
    "https://www.linkedin.com/authwall",
    "https://www.linkedin.com/checkpoint/challenge",
    "https://evil.example/jobs/search/",
])
def test_redirects_are_blocked(target):
    assert collect(FakeClient("", final_url=target), "python", "Vienna, Austria").status == "blocked"


def test_transport_failure_does_not_export_session_details():
    class BrokenClient:
        def get(self, url):
            raise TimeoutError("private session detail")

    result = collect(BrokenClient(), "python", "Vienna, Austria")
    assert result.status == "failed"
    assert "private session" not in result.error


def test_search_values_are_query_parameters_not_an_executable_url():
    parsed = urlsplit(search_url("python & junior", "Vienna, Austria"))
    assert parsed.hostname == "www.linkedin.com"
    assert parse_qs(parsed.query) == {"keywords": ["python & junior"], "location": ["Vienna, Austria"]}
    with pytest.raises(ValueError):
        search_url("", "Vienna")
    with pytest.raises(ValueError):
        collect(FakeClient(""), "python", "Vienna", limit=0)


def test_cli_routes_linkedin_and_uses_its_own_output(monkeypatch, tmp_path):
    client = FakeClient((FIXTURES / "linkedin_search.html").read_text())
    monkeypatch.setattr("job_radar.http.HttpClient", lambda **kwargs: client)
    monkeypatch.chdir(tmp_path)
    args = parser().parse_args(["scrape", "--source", "linkedin", "--transport", "http"])
    _scrape(args)
    result = json.loads((tmp_path / "outputs/linkedin-probe/jobs.json").read_text())
    assert result["jobs"][0]["source"] == "linkedin"
    assert parse_qs(urlsplit(client.calls[0]).query)["location"] == ["Vienna, Austria"]
    assert not list(tmp_path.glob("**/*.db"))
    assert not (tmp_path / "outputs/karriere-probe/jobs.json").exists()
