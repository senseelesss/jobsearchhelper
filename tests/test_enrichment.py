from pathlib import Path

from job_radar.enrichment import enrich_jobs
from job_radar.http import HttpResponse
from job_radar.models import Job
from job_radar.storage import JobRepository

FIXTURES = Path(__file__).parent / "fixtures"


class FakeClient:
    def __init__(self, responses: dict[str, HttpResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str) -> HttpResponse:
        self.calls.append(url)
        return self.responses[url]


def job(
    source: str,
    source_job_id: str,
    *,
    url: str,
    received_at: str,
    snippet: str = "",
    status: str = "eligible",
) -> tuple[Job, str]:
    return (
        Job(
            source=source,
            source_job_id=source_job_id,
            title="Junior Backend Engineer",
            company="Example GmbH",
            location="Vienna, Austria",
            snippet=snippet,
            url=url,
            email_message_id=f"email-{source_job_id}",
            email_received_at=received_at,
            alert_subject="Jobs",
        ),
        status,
    )


def insert_screened(
    repository: JobRepository, values: list[tuple[Job, str]]
) -> None:
    repository.upsert(item for item, _ in values)
    for item, status in values:
        repository.connection.execute(
            "UPDATE jobs SET screening_status = ? WHERE dedupe_key = ?",
            (status, item.dedupe_key),
        )
    repository.connection.commit()


def test_enrichment_fetches_only_canonical_when_it_succeeds(tmp_path) -> None:
    direct_url = "https://www.linkedin.com/jobs/view/direct/"
    alternate_url = "https://at.indeed.com/viewjob?jk=alternate"
    repository = JobRepository(tmp_path / "jobs.db")
    insert_screened(
        repository,
        [
            job(
                "linkedin",
                "direct",
                url=direct_url,
                received_at="2026-06-24T08:00:00Z",
            ),
            job(
                "indeed",
                "alternate",
                url=alternate_url,
                received_at="2026-06-23T08:00:00Z",
            ),
        ],
    )
    client = FakeClient(
        {
            direct_url: HttpResponse(
                200,
                (FIXTURES / "linkedin_vacancy.html").read_text(),
                direct_url,
            )
        }
    )

    try:
        totals = enrich_jobs(repository, client, only="eligible")
        rows = repository.all("eligible")
    finally:
        repository.close()

    canonical = next(row for row in rows if row["source_job_id"] == "direct")
    alternate = next(row for row in rows if row["source_job_id"] == "alternate")
    assert client.calls == [direct_url]
    assert totals == {
        "success": 1,
        "snippet_only": 0,
        "expired": 0,
        "blocked": 0,
        "empty": 0,
        "failed": 0,
    }
    assert canonical["fetch_status"] == "success"
    assert canonical["description_language"] == "de"
    assert canonical["description_hash"]
    assert canonical["description_confidence"] == "medium"
    assert canonical["http_status"] == 200
    assert canonical["fetched_at"]
    assert canonical["final_url"] == direct_url
    assert alternate["fetch_status"] == ""


def test_enrichment_uses_alternate_only_after_canonical_fails(tmp_path) -> None:
    canonical_url = "https://www.linkedin.com/jobs/view/canonical/"
    alternate_url = "https://at.indeed.com/viewjob?jk=alternate"
    repository = JobRepository(tmp_path / "jobs.db")
    insert_screened(
        repository,
        [
            job(
                "linkedin",
                "canonical",
                url=canonical_url,
                received_at="2026-06-24T08:00:00Z",
            ),
            job(
                "indeed",
                "alternate",
                url=alternate_url,
                received_at="2026-06-23T08:00:00Z",
            ),
        ],
    )
    client = FakeClient(
        {
            canonical_url: HttpResponse(
                200,
                (FIXTURES / "vacancy_blocked.html").read_text(),
                canonical_url,
            ),
            alternate_url: HttpResponse(
                200,
                (FIXTURES / "indeed_vacancy.html").read_text(),
                alternate_url,
            ),
        }
    )

    try:
        totals = enrich_jobs(repository, client, only="eligible")
        canonical = next(
            row
            for row in repository.all("eligible")
            if row["source_job_id"] == "canonical"
        )
    finally:
        repository.close()

    assert client.calls == [canonical_url, alternate_url]
    assert totals["success"] == 1
    assert totals["blocked"] == 0
    assert canonical["fetch_status"] == "success"
    assert canonical["final_url"] == alternate_url
    assert "Build Python services" in canonical["description"]


def test_successful_cache_is_reused_without_http_request(tmp_path) -> None:
    url = "https://at.indeed.com/viewjob?jk=cached"
    repository = JobRepository(tmp_path / "jobs.db")
    insert_screened(
        repository,
        [
            job(
                "indeed",
                "cached",
                url=url,
                received_at="2026-06-23T08:00:00Z",
            )
        ],
    )
    first_client = FakeClient(
        {
            url: HttpResponse(
                200,
                (FIXTURES / "indeed_vacancy.html").read_text(),
                url,
            )
        }
    )
    try:
        enrich_jobs(repository, first_client, only="eligible")
        repository.connection.execute(
            """
            UPDATE jobs
            SET description = '', description_language = '', fetched_at = '',
                fetch_status = '', fetch_error = '', final_url = '',
                description_hash = ''
            """
        )
        repository.connection.commit()
        second_client = FakeClient({})
        totals = enrich_jobs(repository, second_client, only="eligible")
        row = repository.all("eligible")[0]
    finally:
        repository.close()

    assert first_client.calls == [url]
    assert second_client.calls == []
    assert totals["success"] == 1
    assert row["description_hash"]


def test_retry_failed_bypasses_failed_cache(tmp_path) -> None:
    url = "https://www.linkedin.com/jobs/view/retry/"
    repository = JobRepository(tmp_path / "jobs.db")
    insert_screened(
        repository,
        [
            job(
                "linkedin",
                "retry",
                url=url,
                received_at="2026-06-23T08:00:00Z",
            )
        ],
    )
    blocked_client = FakeClient(
        {
            url: HttpResponse(
                200,
                (FIXTURES / "vacancy_blocked.html").read_text(),
                url,
            )
        }
    )
    try:
        enrich_jobs(repository, blocked_client, only="eligible")
        skipped_client = FakeClient({})
        skipped = enrich_jobs(repository, skipped_client, only="eligible")
        retry_client = FakeClient(
            {
                url: HttpResponse(
                    200,
                    (FIXTURES / "linkedin_vacancy.html").read_text(),
                    url,
                )
            }
        )
        retried = enrich_jobs(
            repository, retry_client, only="eligible", retry_failed=True
        )
    finally:
        repository.close()

    assert skipped_client.calls == []
    assert skipped["blocked"] == 1
    assert retry_client.calls == [url]
    assert retried["success"] == 1


def test_blocked_indeed_uses_email_snippet_fallback(tmp_path) -> None:
    url = "https://at.indeed.com/viewjob?jk=blocked"
    repository = JobRepository(tmp_path / "jobs.db")
    insert_screened(
        repository,
        [
            job(
                "indeed",
                "blocked",
                url=url,
                received_at="2026-06-23T08:00:00Z",
                snippet="Build Python automations with the engineering team.",
            )
        ],
    )
    client = FakeClient(
        {
            url: HttpResponse(
                403,
                (FIXTURES / "indeed_consent.html").read_text(),
                "https://at.indeed.com/challenge",
            )
        }
    )

    try:
        totals = enrich_jobs(repository, client, only="eligible")
        row = repository.all("eligible")[0]
    finally:
        repository.close()

    assert totals["snippet_only"] == 1
    assert row["fetch_status"] == "snippet_only"
    assert row["description_confidence"] == "low"
    assert row["description_language"] == "en"
    assert row["http_status"] == 403
    assert row["final_url"] == "https://at.indeed.com/challenge"
    assert row["fetch_error"] == "HTTP 403"


def test_expired_indeed_snippet_preserves_expired_reason(tmp_path) -> None:
    url = "https://at.indeed.com/viewjob?jk=expired"
    repository = JobRepository(tmp_path / "jobs.db")
    insert_screened(
        repository,
        [
            job(
                "indeed",
                "expired",
                url=url,
                received_at="2026-06-23T08:00:00Z",
                snippet="Student role in a Vienna engineering team.",
            )
        ],
    )
    client = FakeClient(
        {
            url: HttpResponse(
                200,
                (FIXTURES / "vacancy_expired.html").read_text(),
                url,
            )
        }
    )

    try:
        enrich_jobs(repository, client, only="eligible")
        row = repository.all("eligible")[0]
    finally:
        repository.close()

    assert row["fetch_status"] == "snippet_only"
    assert row["fetch_error"] == "expired listing"
    assert row["http_status"] == 200
