from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from job_radar.collection import collect_search
from job_radar.daily import daily_cycle
from job_radar.http import HttpResponse
from job_radar.ingestion import ingest_observations
from job_radar.models import Job
from job_radar.searches import load_searches, validate_search_url
from job_radar.sources.base import CollectionBatch, SourceVacancy
from job_radar.storage import JobRepository

URL = "https://www.karriere.at/jobs/python/wien?employmentTypes=2&remote=true"
BODY = (
    (Path(__file__).parent / "fixtures/karriere_search.html")
    .read_text()
    .replace("3 Python Jobs", "20 Python Jobs")
)


class Pages:
    def __init__(self, pages, details=None):
        self.pages = pages
        self.details = details or {}
        self.calls = []
        self.closed = False

    def iter_search_pages(self, url, source, max_pages):
        try:
            for page in self.pages[:max_pages]:
                if isinstance(page, Exception):
                    raise page
                yield HttpResponse(200, page, url)
        finally:
            self.closed = True

    def get(self, url):
        self.calls.append(url)
        return self.details[url]


def observation():
    return SourceVacancy(
        "linkedin",
        "1234567890",
        "Junior Python Developer",
        "Example GmbH",
        "Vienna, Austria",
        "",
        "https://www.linkedin.com/jobs/view/1234567890/",
        "2026-11-04T12:00:00+00:00",
        "linkedin-public-search-v1",
    )


def test_cumulative_pages_are_merged_and_closed():
    second = BODY + BODY.replace("1234567", "3333333").replace("7654321", "4444444")
    client = Pages([BODY, second])
    result = collect_search(client, URL, limit=10, max_pages=2)
    assert len(result.jobs) == 4
    assert result.pages_fetched == 2
    assert client.closed
    assert all(job.search_url == URL for job in result.jobs)
    assert not result.coverage_complete


@pytest.mark.parametrize(
    "second,reason", [(BODY, "repeated_page"), (TimeoutError(), "transport_failure")]
)
def test_partial_results_survive_repeats_and_late_failure(second, reason):
    client = Pages([BODY, second])
    result = collect_search(client, URL, limit=10, max_pages=3)
    assert result.status == "partial"
    assert len(result.jobs) == 2
    assert result.stop_reason == reason
    assert not result.coverage_complete
    assert client.closed


def test_details_are_bounded_and_failure_preserves_search_record():
    url = "https://www.karriere.at/jobs/1234567"
    client = Pages(
        [BODY],
        {
            url: HttpResponse(
                200,
                '<div class="m-jobContent"><div class="sja"><p>Build Python APIs and services with our team.</p></div></div>',
                url,
            )
        },
    )
    result = collect_search(client, URL, details=1)
    assert result.jobs[0].description_kind == "full"
    assert "Build Python" in result.jobs[0].description
    assert len(client.calls) == 1
    assert result.jobs[1].description_kind == "snippet"
    client.details[url] = HttpResponse(403, "Access denied", url)
    blocked = collect_search(client, URL, details=1)
    assert blocked.status == "partial"
    assert len(blocked.jobs) == 2
    assert blocked.jobs[0].fetch_status == "blocked"


@pytest.mark.parametrize(
    "url",
    [
        "http://www.linkedin.com/jobs/search/",
        "https://localhost/jobs/search/",
        "https://linkedin.com.evil.example/jobs/search/",
        "https://www.linkedin.com/authwall",
        "https://www.karriere.at/jobs/1234567",
        "https://user:pass@www.karriere.at/jobs/python",
        "https://www.linkedin.com:8080/jobs/search/",
        "https://www.linkedin.com/jobs/search/?access_token=secret",
    ],
)
def test_only_public_search_urls_are_accepted(url):
    with pytest.raises(ValueError):
        validate_search_url(url)


def test_filter_values_are_preserved_and_tracking_removed(tmp_path):
    search = validate_search_url(
        "https://at.linkedin.com/jobs/search/?keywords=python&f_E=1%2C2&f_WT=2&f_WT=3&trk=remove#fragment"
    )
    assert parse_qs(urlsplit(search.url).query) == {
        "keywords": ["python"],
        "f_E": ["1,2"],
        "f_WT": ["2", "3"],
    }
    path = tmp_path / "searches.yaml"
    path.write_text(f"searches:\n  - name: junior\n    url: '{search.url}'\n")
    assert load_searches(path)[0].url == search.url


def test_email_direct_replay_preserves_provenance_and_application_history(tmp_path):
    with JobRepository(tmp_path / "jobs.db") as repository:
        item = observation()
        first = ingest_observations(repository, [item])
        assert first.inserted == 1
        assert repository.all()[0]["email_message_id"] == ""
        repository.set_application(item.dedupe_key, "applied", "request1", expected_revision=0)
        email = Job(
            item.source,
            item.source_job_id,
            item.title,
            item.company,
            item.location,
            "",
            item.url,
            "mail1",
            "2026-11-04T12:00:00+00:00",
            "Alert",
        )
        repository.upsert([email])
        repeated = ingest_observations(repository, [item, item])
        assert repeated.inserted == 0
        assert len(repository.all()) == 1
        assert repository.all()[0]["email_message_id"] == "mail1"
        assert repository.application_for(item.dedupe_key)["state"] == "applied"
        repository.upsert([replace(email, title="Updated Python Role")])
        assert repository.application_for(item.dedupe_key)["state"] == "applied"
        assert (
            repository.set_application(item.dedupe_key, "applied", "request1", expected_revision=0)
            == "replay"
        )
        with pytest.raises(ValueError, match="conflict"):
            repository.set_application(item.dedupe_key, "skipped", "request2", expected_revision=0)
        assert (
            repository.connection.execute("SELECT COUNT(*) FROM application_events").fetchone()[0]
            == 1
        )


def test_daily_email_failure_does_not_cancel_direct_and_replay_is_idempotent(tmp_path):
    def broken_email():
        raise RuntimeError("private oauth detail")

    item = observation()
    search = validate_search_url("https://www.linkedin.com/jobs/search/?keywords=python", "junior")

    def collect(url):
        return CollectionBatch("success", url, jobs=(item,))

    path = tmp_path / "jobs.db"
    first = daily_cycle(path, [search], collect, fetch_emails=broken_email, output=tmp_path / "out")
    assert first["status"] == "partial"
    assert first["inserted"] == 1
    with JobRepository(path) as repository:
        repository.set_application(item.dedupe_key, "applied", "manual")
    second = daily_cycle(path, [search], collect, output=tmp_path / "out")
    assert second["status"] == "success"
    assert second["inserted"] == 0
    assert second["review_count"] == 0
    assert (tmp_path / "out/vacancies.xlsx").exists()


@pytest.mark.parametrize("fail", [False, True])
def test_daily_delivery_is_not_resent_after_ack_or_unknown_send(tmp_path, fail):
    class Telegram:
        calls = 0

        def send_message(self, *args):
            self.calls += 1
            if fail:
                raise TimeoutError()
            return 42

    telegram = Telegram()
    path = tmp_path / "jobs.db"
    for _ in range(2):
        result = daily_cycle(
            path,
            [],
            lambda url: None,
            telegram=telegram,
            chat_id="private-chat-id",
            output=tmp_path / "out",
        )
    assert telegram.calls == 1
    assert result["telegram"] == ("unknown" if fail else "sent")
    if fail:
        assert result["status"] == "partial"
    with JobRepository(path) as repo:
        assert (
            "private-chat-id"
            not in repo.connection.execute(
                "SELECT notification_key FROM notification_outbox"
            ).fetchone()[0]
        )


def test_http_continuation_uses_observed_card_count_and_preserves_filters():
    body = (Path(__file__).parent / "fixtures/linkedin_search.html").read_text()

    class Client:
        calls = []

        def get(self, url):
            index = len(self.calls)
            self.calls.append(url)
            content = body.replace("4428938286", str(5528938286 + index)).replace(
                "1234567890", str(2234567890 + index)
            )
            return HttpResponse(200, content, url)

    client = Client()
    result = collect_search(
        client,
        "https://www.linkedin.com/jobs/search/?keywords=python&f_E=1%2C2&start=20",
        limit=20,
        max_pages=3,
    )
    assert result.status == "success"
    assert len(result.jobs) == 6
    assert [parse_qs(urlsplit(url).query)["start"] for url in client.calls] == [
        ["20"],
        ["23"],
        ["26"],
    ]
    assert all(parse_qs(urlsplit(url).query)["f_E"] == ["1,2"] for url in client.calls)


def test_daily_score_budget_advances_past_cached_jobs_and_reports_cost(tmp_path):
    from job_radar.ai.provider import ScoreProviderResult, TokenUsage
    from job_radar.application_models import JobScore

    class Provider:
        model = "test-model"
        calls = []

        def score(self, profile, vacancy, provisional):
            self.calls.append(vacancy["dedupe_key"])
            return ScoreProviderResult(
                JobScore(
                    match_score=80,
                    score_confidence="high",
                    provisional=provisional,
                    recommendation="apply",
                    matching_strengths=["Python"],
                    missing_requirements=[],
                    risk_flags=[],
                    role_summary="Python role",
                ),
                TokenUsage(100, 50, 150),
                "synthetic-response",
            )

    first = observation()
    second = replace(
        first,
        source_job_id="2222222222",
        title="Junior Python Automation Engineer",
        url="https://www.linkedin.com/jobs/view/2222222222/",
    )
    search = validate_search_url("https://www.linkedin.com/jobs/search/?keywords=python", "python")

    def collect(url):
        return CollectionBatch("success", url, jobs=(first, second))

    provider = Provider()
    for _ in range(2):
        result = daily_cycle(
            tmp_path / "jobs.db",
            [search],
            collect,
            provider=provider,
            profile={},
            score_limit=1,
            input_cost_per_million=2,
            output_cost_per_million=4,
            output=tmp_path / "out",
        )
        assert result["status"] == "success"
        assert result["estimated_cost_usd"] > 0
    assert len(provider.calls) == len(set(provider.calls)) == 2


def test_older_email_cannot_overwrite_newer_source_fields(tmp_path):
    with JobRepository(tmp_path / "jobs.db") as repository:
        newer = Job(
            "linkedin",
            "123",
            "Current Python Role",
            "Current Company",
            "Vienna",
            "Current text",
            "https://www.linkedin.com/jobs/view/123/",
            "new-message",
            "Wed, 04 Nov 2026 12:00:00 +0000",
            "Latest",
        )
        repository.upsert([newer])
        older = replace(
            newer,
            title="Old Role",
            company="Old Company",
            email_message_id="old-message",
            email_received_at="Tue, 03 Nov 2026 12:00:00 +0000",
        )
        result = repository.upsert_detailed([older])
        assert result.unchanged == 1
        row = repository.all()[0]
        assert row["title"] == "Current Python Role"
        assert row["email_message_id"] == "new-message"
