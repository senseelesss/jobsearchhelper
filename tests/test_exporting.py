from job_radar.exporting import collapse_semantic
from job_radar.models import Job
from job_radar.storage import JobRepository


def job(
    source: str,
    source_job_id: str,
    *,
    received_at: str,
    snippet: str,
    url: str,
) -> Job:
    return Job(
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
    )


def test_collapse_semantic_selects_canonical_and_preserves_source_rows(tmp_path) -> None:
    jobs = [
        job(
            "indeed",
            "sponsored-newest",
            received_at="2026-06-25T08:00:00Z",
            snippet="The longest sponsored snippet in the group.",
            url="https://at.indeed.com/pagead/clk/dl?ad=newest",
        ),
        job(
            "linkedin",
            "direct-old",
            received_at="2026-06-22T08:00:00Z",
            snippet="The longest direct snippet, but it is older.",
            url="https://www.linkedin.com/jobs/view/direct-old/",
        ),
        job(
            "indeed",
            "direct-new-short",
            received_at="2026-06-23T08:00:00Z",
            snippet="Short.",
            url="https://at.indeed.com/viewjob?jk=direct-new-short",
        ),
        job(
            "linkedin",
            "direct-new-long",
            received_at="Tue, 23 Jun 2026 08:00:00 +0000",
            snippet="Longer snippet wins when directness and received time match.",
            url="https://www.linkedin.com/jobs/view/direct-new-long/",
        ),
    ]
    repository = JobRepository(tmp_path / "jobs.db")
    try:
        repository.upsert(jobs)
        source_rows = repository.all()
        collapsed = collapse_semantic(source_rows)
        rows_after_export = repository.all()
    finally:
        repository.close()

    assert len(source_rows) == 4
    assert len(rows_after_export) == 4
    assert len(collapsed) == 1
    assert collapsed[0]["source_job_id"] == "direct-new-long"
    assert collapsed[0]["semantic_duplicate_count"] == 3
    assert collapsed[0]["alternate_source_urls"] == [
        "https://at.indeed.com/viewjob?jk=direct-new-short",
        "https://www.linkedin.com/jobs/view/direct-old/",
        "https://at.indeed.com/pagead/clk/dl?ad=newest",
    ]


def test_collapse_semantic_marks_unique_row_with_zero_duplicates(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    try:
        repository.upsert(
            [
                job(
                    "linkedin",
                    "unique",
                    received_at="2026-06-23T08:00:00Z",
                    snippet="",
                    url="https://www.linkedin.com/jobs/view/unique/",
                )
            ]
        )
        collapsed = collapse_semantic(repository.all())
    finally:
        repository.close()

    assert collapsed[0]["semantic_duplicate_count"] == 0
    assert collapsed[0]["alternate_source_urls"] == []
