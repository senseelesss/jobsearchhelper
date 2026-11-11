"""Deterministic collection/enrichment v2 acceptance and confirmed audit regressions."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from job_radar.collection import collect_search
from job_radar.daily import daily_cycle, review_rows
from job_radar.enrichment import enrich_jobs
from job_radar.enrichment_adapters import adapter_for
from job_radar.enrichment_adapters.ats import application_link, public_posting, parse_posting
from job_radar.enrichment_adapters.common import completeness, extract_json_ld_description
from job_radar.ingestion import ingest_observations
from job_radar.models import Job
from job_radar.searches import validate_search_url
from job_radar.sources.registry import source_adapter
from job_radar.sources.base import CollectionBatch
from job_radar.storage import JobRepository
from job_radar.http import HttpResponse
from job_radar.screening import screen_job
from test_production import Provider, SEARCH, job

FIXTURES = Path(__file__).parent / "fixtures"
FULL = (FIXTURES / "structured_job.html").read_text()


class Client:
    def __init__(self, body=FULL, status=200):
        self.body, self.status, self.calls = body, status, []

    def get(self, url):
        self.calls.append(url)
        if isinstance(self.body, Exception):
            raise self.body
        return HttpResponse(self.status, self.body, url)


def run(path, items=(), **kwargs):
    return daily_cycle(
        path / "jobs.db",
        [SEARCH],
        lambda url: CollectionBatch("success", url, jobs=tuple(items)),
        output=path / "out",
        profile={},
        score_model="test-model",
        **kwargs,
    )


@pytest.mark.parametrize(
    "source,url,fixture",
    [
        (
            "absolventen",
            "https://www.absolventen.at/?feed=job_feed&search_location=Wien",
            "absolventen_jobs.xml",
        ),
        (
            "greenhouse",
            "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true",
            "greenhouse_board.json",
        ),
        ("lever", "https://api.lever.co/v0/postings/example?mode=json", "lever_board.json"),
    ],
)
def test_public_feed_discovery_ingestion_and_idempotency(tmp_path, source, url, fixture):
    search = validate_search_url(url)
    assert search.source == source
    client = Client((FIXTURES / fixture).read_text())
    result = collect_search(client, url, max_pages=3)
    assert result.status == "success"
    assert result.pages_fetched == len(client.calls) == 1
    assert len(result.jobs) == 1
    with JobRepository(tmp_path / "jobs.db") as repo:
        assert ingest_observations(repo, result.jobs).inserted == 1
        assert ingest_observations(repo, result.jobs).unchanged == 1
        assert len(repo.all()) == 1
        if source != "absolventen":
            assert repo.all()[0]["fetch_status"] == "success"
            assert "20 hours" in repo.all()[0]["description"]


@pytest.mark.parametrize(
    "source,url,body",
    [
        (
            "absolventen",
            "https://www.absolventen.at/?feed=job_feed",
            "<rss><channel><title>Jobs</title></channel></rss>",
        ),
        (
            "greenhouse",
            "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true",
            '{"jobs":[]}',
        ),
        ("lever", "https://api.lever.co/v0/postings/example?mode=json", "[]"),
    ],
)
def test_new_sources_empty_changed_layout_and_rate_limit(source, url, body):
    adapter = source_adapter(source)
    assert adapter.parse_search(body, url).status == "empty"
    assert adapter.parse_search("<html>cookie banner</html>", url).status == "failed"
    assert adapter.parse_search(body, url, status_code=429).status == "rate_limited"


@pytest.mark.parametrize(
    "body",
    [
        '<div class="job_description">Accept all cookies. Cookie settings</div>',
        '<div class="job_description">Login to continue</div>',
        '<div class="job_description">Enable JavaScript to view this page</div>',
        '<div class="job_description">Join our talent community and subscribe to job alerts.</div>',
        '<div class="job_description">Build Python services...</div>',
        "<html><nav>Jobs About Contact</nav></html>",
    ],
)
def test_false_full_description_rejected(body):
    assert adapter_for("absolventen").parse(body, 200).status != "success"


def test_jsonld_metadata_and_multiple_posting_ambiguity():
    page = adapter_for("linkedin").parse(FULL, 200)
    assert page.completeness == "FULL"
    assert page.metadata["baseSalary"]["value"]["unitText"] == "YEAR"
    assert "Employment type: PART_TIME" in page.description
    assert extract_json_ld_description(FULL + FULL) == ""


@pytest.mark.parametrize(
    "status,text,error,state",
    [
        ("success", "Verified JD", "", "FULL"),
        ("snippet_only", "Short", "", "PARTIAL"),
        ("empty", "", "", "MISSING"),
        ("blocked", "", "", "INACCESSIBLE"),
        ("snippet_only", "Short", "expired listing", "EXPIRED"),
    ],
)
def test_completeness_states(status, text, error, state):
    assert completeness(status, text, error) == state


def test_cached_ai_score_does_not_block_enrichment_and_changed_hash_invalidates_it(tmp_path):
    provider = Provider()
    run(tmp_path, [job()], provider=provider, score_limit=1)
    client = Client()
    result = run(tmp_path, enrichment_client=client, enrichment_limit=1)
    assert len(client.calls) == result["enrichment"]["requests"] == 1
    assert result["funnel"]["database_totals"]["full_jd_active"] == 1
    assert result["funnel"]["database_totals"]["current_scored"] == 0
    # Re-scoring must still be explicitly budgeted.
    assert len(provider.calls) == 1
    run(tmp_path, provider=provider, score_limit=1)
    assert len(provider.calls) == 2


def test_failed_enrichment_changes_overall_run_health_and_survives_restart(tmp_path):
    result = run(
        tmp_path,
        [job()],
        enrichment_client=Client(TimeoutError("private-token")),
        enrichment_limit=1,
    )
    assert result["status"] == "partial"
    assert result["outcome"] == "PARTIAL_SUCCESS"
    with JobRepository(tmp_path / "jobs.db") as repo:
        attempt = repo.enrichment_attempt(job().url)
        assert attempt["attempts"] == 1 and attempt["last_status"] == "failed"
        assert "private-token" not in json.dumps(result)
    client = Client()
    result = run(tmp_path, enrichment_client=client, enrichment_limit=1)
    assert not client.calls


def test_stale_full_description_refreshed_once_and_failed_refresh_keeps_hash(tmp_path):
    client = Client()
    run(tmp_path, [job()], enrichment_client=client, enrichment_limit=1)
    with JobRepository(tmp_path / "jobs.db") as repo:
        original = repo.all()[0]
        old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        repo.connection.execute("UPDATE jobs SET fetched_at=?", (old,))
        repo.connection.execute("UPDATE enrichment_cache SET fetched_at=?", (old,))
        repo.connection.commit()
    failure = Client("<html>unavailable</html>", 503)
    result = run(tmp_path, enrichment_client=failure, enrichment_limit=1)
    assert result["enrichment"]["refresh_failed"] == 1
    assert result["status"] == "partial"
    with JobRepository(tmp_path / "jobs.db") as repo:
        assert repo.all()[0]["description_hash"] == original["description_hash"]
        assert repo.all()[0]["description"] == original["description"]
    run(tmp_path, enrichment_client=failure, enrichment_limit=1)
    assert len(failure.calls) == 1


def test_changed_discovery_snippet_and_lower_quality_detail_preserve_full_jd(tmp_path):
    run(tmp_path, [job()], enrichment_client=Client(), enrichment_limit=1)
    with JobRepository(tmp_path / "jobs.db") as repo:
        before = repo.all()[0]
        ingest_observations(repo, [replace(job(), snippet="Changed alert snippet")])
        assert repo.all()[0]["description_hash"] == before["description_hash"]
        ingest_observations(
            repo,
            [
                replace(
                    job(),
                    fetch_status="snippet_only",
                    description="Short...",
                    snippet="Changed again",
                )
            ],
        )
        assert repo.all()[0]["description_hash"] == before["description_hash"]


def test_ats_direct_link_recovery_preserves_aggregator_and_budget(tmp_path):
    posting = "https://job-boards.greenhouse.io/example/jobs/123"
    doc = '<a href="' + posting + '">Apply</a>'
    body = json.loads((FIXTURES / "greenhouse_board.json").read_text())["jobs"][0]
    body["title"] = job().title

    class ATSClient(Client):
        def get(self, url):
            self.calls.append(url)
            return HttpResponse(200, json.dumps(body) if "boards-api." in url else doc, url)

    client = ATSClient()
    run(tmp_path, [job()], enrichment_client=client, enrichment_limit=2)
    assert len(client.calls) == 2
    with JobRepository(tmp_path / "jobs.db") as repo:
        row = repo.all()[0]
        assert row["url"] == job().url
        assert row["source"] == "linkedin" and row["fetch_status"] == "success"
        assert review_rows(repo)[0]["jd_provenance"]["application_url"] == posting
    # A one-request budget must never make the second request.
    other = tmp_path / "bounded"
    other.mkdir()
    limited = ATSClient()
    run(other, [job()], enrichment_client=limited, enrichment_limit=1)
    assert len(limited.calls) == 1


def test_ats_resolver_never_guesses_board_or_follows_untrusted_links():
    assert public_posting("https://jobs.lever.co/example/12345678-1234-1234-1234-123456789abc")
    assert public_posting("https://jobs.eu.lever.co/example/12345678-1234-1234-1234-123456789abc")[
        1
    ].startswith("https://api.eu.lever.co/")
    for url in [
        "http://boards.greenhouse.io/example/jobs/123",
        "https://boards.greenhouse.io.evil.test/example/jobs/123",
        "https://boards.greenhouse.io/example",
        "https://user:secret@boards.greenhouse.io/example/jobs/123",
    ]:
        assert public_posting(url) is None
    assert (
        application_link(
            '<a href="https://boards.greenhouse.io/example/jobs/1">A</a><a href="https://boards.greenhouse.io/example/jobs/2">B</a>'
        )
        == ""
    )
    assert parse_posting("not json", 200, "lever").status == "failed"


def test_remote_austria_office_city_and_country_restrictions():
    assert (
        screen_job(
            "Junior Python Engineer", "Graz", "Remote work from Austria. 20 hours/week."
        ).status
        == "eligible"
    )
    assert (
        screen_job(
            "Junior Python Engineer", "Graz", "Remote Germany only. Must reside in Germany."
        ).status
        == "rejected"
    )


def test_sparse_identity_does_not_merge_different_native_jobs():
    a = Job("linkedin", "1", "Junior Python", "", "Vienna", "", "https://example.test/1")
    assert a.semantic_key != replace(a, source="karriere", source_job_id="2").semantic_key
    assert (
        replace(a, company="Example").semantic_key
        == replace(a, company="Example", source="karriere").semantic_key
    )


def test_legacy_database_additive_migration_and_backup_preserves_state(tmp_path):
    from job_radar.backups import backup_database

    run(tmp_path, [job()])
    with JobRepository(tmp_path / "jobs.db") as repo:
        repo.connection.execute("DROP TABLE enrichment_attempts")
        repo.connection.commit()
        old = repo.all()[0]
    with JobRepository(tmp_path / "jobs.db") as repo:
        assert repo.all()[0]["dedupe_key"] == old["dedupe_key"]
        assert repo.enrichment_attempt(job().url) is None
    snapshot = backup_database(tmp_path / "jobs.db", tmp_path / "backups")
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_retry_attempt_cap_is_durable_and_explicit_refresh_overrides_it(tmp_path):
    run(tmp_path, [job()])
    with JobRepository(tmp_path / "jobs.db") as repo:
        for _ in range(5):
            repo.record_enrichment_attempt(job().url, "failed", 1, {}, 0)
        client = Client()
        result = enrich_jobs(
            repo, client, retry_failed=True, respect_cooldown=True, request_limit=1
        )
        assert not client.calls and result["deferred"] == 1
        enrich_jobs(
            repo, client, retry_failed=True, respect_cooldown=True, request_limit=1, refresh=True
        )
        assert len(client.calls) == 1
        assert repo.enrichment_attempt(job().url)["attempts"] == 0


def test_unrelated_ats_posting_does_not_replace_aggregator_description(tmp_path):
    body = json.loads((FIXTURES / "greenhouse_board.json").read_text())["jobs"][0]
    body["title"] = "Different Position"

    class Mismatch(Client):
        def get(self, url):
            self.calls.append(url)
            if "boards-api." in url:
                return HttpResponse(200, json.dumps(body), url)
            return HttpResponse(
                200, FULL + '<a href="https://boards.greenhouse.io/example/jobs/123">Apply</a>', url
            )

    run(tmp_path, [job()], enrichment_client=Mismatch(), enrichment_limit=2)
    with JobRepository(tmp_path / "jobs.db") as repo:
        assert "Work remotely from Austria" in repo.all()[0]["description"]
        assert repo.all()[0]["final_url"] == job().url


def test_cross_source_group_retains_application_state_and_native_observations(tmp_path):
    first = job(title="Junior Python Developer")
    second = replace(
        first, source="karriere", source_job_id="99", url="https://www.karriere.at/jobs/99"
    )
    with JobRepository(tmp_path / "jobs.db") as repo:
        ingest_observations(repo, [first])
        repo.set_application(first.dedupe_key, "applied", "submitted-once")
        ingest_observations(repo, [second])
        rows = review_rows(repo)
        assert len(rows) == 1 and len(rows[0]["source_keys"]) == 2
        assert rows[0]["application_state"] == "applied"
        assert repo.connection.execute("SELECT COUNT(*) FROM job_observations").fetchone()[0] == 2
        assert repo.application_for(second.dedupe_key)["state"] == "applied"
    with JobRepository(tmp_path / "jobs.db") as repo:
        assert review_rows(repo)[0]["application_state"] == "applied"


def test_different_titles_at_same_employer_remain_distinct(tmp_path):
    with JobRepository(tmp_path / "jobs.db") as repo:
        ingest_observations(repo, [job(), job(2, title="Junior QA Automation Engineer")])
        assert len(review_rows(repo)) == 2


def test_http_retry_after_is_honored_or_deferred_without_early_retry(monkeypatch):
    import io
    import urllib.error
    from email.message import Message
    from job_radar.http import HttpClient

    calls, sleeps = [], []
    headers = Message()
    headers["Retry-After"] = "120"

    def fail(request, timeout):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(
            request.full_url, 429, "limited", headers, io.BytesIO(b"limited")
        )

    monkeypatch.setattr("urllib.request.urlopen", fail)
    monkeypatch.setattr("time.sleep", sleeps.append)
    client = HttpClient(1, 0, 2, max_backoff_seconds=10)
    assert client.get("https://example.test/job").status_code == 429
    assert len(calls) == 1 and sleeps == []
    assert client.metrics["http_errors"] == 1
    headers.replace_header("Retry-After", "2")
    client.get("https://example.test/job")
    assert len(calls) == 4 and sleeps == [2, 2]


def test_http_same_host_policy_refuses_external_redirect(monkeypatch):
    import io
    from email.message import Message
    from job_radar.http import HttpClient

    headers = Message()

    class Opener:
        def __init__(self, handler):
            self.handler = handler

        def open(self, request, timeout):
            self.handler.redirect_request(
                request, io.BytesIO(b""), 302, "redirect", headers, "https://external.test/job"
            )
            pytest.fail("External redirect should not be followed")

    monkeypatch.setattr("urllib.request.build_opener", lambda handler: Opener(handler))
    assert (
        HttpClient(1, 0, 0, same_host_redirects=True)
        .get("https://www.absolventen.at/job/test/")
        .status_code
        == 403
    )


def test_confirmed_expiry_retains_original_text_and_excludes_job(tmp_path):
    run(tmp_path, [job()], enrichment_client=Client(), enrichment_limit=1)
    with JobRepository(tmp_path / "jobs.db") as repo:
        text = repo.all()[0]["description"]
        enrich_jobs(repo, Client("", 410), refresh=True, request_limit=1)
        row = repo.all()[0]
        assert row["description"] == text
        assert row["fetch_status"] == "expired"
        assert review_rows(repo)[0]["jd_completeness"] == "EXPIRED"


def test_structured_salary_basis_is_not_employment_requirement():
    from job_radar.work_hours import assess_hours

    description = adapter_for("linkedin").parse(FULL, 200).description
    assert assess_hours({"description": description})["hours_fit"] == "confirmed_target"


def test_feed_parser_rejects_malformed_records_without_masking_valid_jobs():
    doc = (
        (FIXTURES / "absolventen_jobs.xml")
        .read_text()
        .replace(
            "</channel>",
            "<item><title>Broken</title><link>https://evil.test/job/1</link></item></channel>",
        )
    )
    batch = source_adapter("absolventen").parse_search(
        doc, "https://www.absolventen.at/?feed=job_feed"
    )
    assert batch.status == "partial" and batch.invalid_cards == 1 and len(batch.jobs) == 1


def test_sparse_identity_remains_distinct_after_screen_and_reopen(tmp_path):
    with JobRepository(tmp_path / "jobs.db") as repo:
        ingest_observations(repo, [job(company=""), job(2, title=job().title, company="")])
        repo.screen_all()
        assert len(review_rows(repo)) == 2
    with JobRepository(tmp_path / "jobs.db") as repo:
        assert len(review_rows(repo)) == 2


def test_partial_detail_text_is_retained_for_future_enrichment(tmp_path):
    run(
        tmp_path,
        [job()],
        enrichment_client=Client(
            '<div class="show-more-less-html__markup">Build Python services with SQL and automated tests...</div>'
        ),
        enrichment_limit=1,
    )
    with JobRepository(tmp_path / "jobs.db") as repo:
        assert "automated tests" in repo.all()[0]["description"]
        assert review_rows(repo)[0]["jd_completeness"] == "PARTIAL"


def test_structured_expiry_is_evidence_but_invalid_deadline_is_not():
    expired = FULL.replace("2099-01-01", "2000-01-01")
    assert adapter_for("linkedin").parse(expired, 200).status == "expired"
    invalid = FULL.replace("2099-01-01", "invalid")
    assert adapter_for("linkedin").parse(invalid, 200).status == "success"


def test_expired_snippet_http_reason_remains_expired():
    assert completeness("snippet_only", "Alert snippet", "HTTP 404") == "EXPIRED"
