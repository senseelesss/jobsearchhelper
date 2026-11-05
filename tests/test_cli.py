import json
from pathlib import Path

from job_radar.cli import (
    _backfill,
    _application_report,
    _enrich,
    _enrichment_report,
    _export,
    _score,
    _screen,
    parser,
)
from job_radar.config import Settings
from job_radar.candidate import candidate_profile_hash, load_candidate_profile
from job_radar.enrichment_adapters.common import description_hash
from job_radar.matching import DRAFT_PROMPT_VERSION, SCORE_PROMPT_VERSION
from job_radar.models import Job
from job_radar.storage import JobRepository


class _Args:
    days = 10
    query = None
    limit = None
    dry_run = True


class _DummyService:
    pass


def test_backfill_dry_run_hides_sponsored_urls(monkeypatch, tmp_path, capsys) -> None:
    from job_radar.models import EmailMessage

    jobs = [
        Job(
            source="indeed",
            source_job_id="sponsored-123",
            title="Automation Intern",
            company="Alpha Systems GmbH",
            location="Wien, W",
            snippet="",
            url="https://at.indeed.com/pagead/clk/dl?ad=alpha123&from=ja&tracking=one",
            email_message_id="m1",
            email_received_at="2026-06-23T08:00:00Z",
            alert_subject="Jobs",
        ),
        Job(
            source="indeed",
            source_job_id="bitpanda456",
            title="Junior Backend Engineer",
            company="Bitpanda GmbH",
            location="Wien, W",
            snippet="",
            url="https://at.indeed.com/viewjob?jk=bitpanda456",
            email_message_id="m1",
            email_received_at="2026-06-23T08:00:00Z",
            alert_subject="Jobs",
        ),
    ]

    monkeypatch.setattr(
        "job_radar.gmail.gmail_service", lambda *args, **kwargs: _DummyService()
    )
    monkeypatch.setattr(
        "job_radar.gmail.fetch_messages",
        lambda *args, **kwargs: [
            EmailMessage(
                message_id="m1",
                received_at="2026-06-23T08:00:00Z",
                sender="Indeed <donotreply@jobalert.indeed.com>",
                subject="Jobs",
                text="",
                html="",
            )
        ],
    )
    monkeypatch.setattr("job_radar.pipeline.extract_jobs", lambda messages: jobs)

    settings = Settings(
        database_path=Path(tmp_path / "jobs.db"),
        credentials_path=Path(tmp_path / "credentials.json"),
        token_path=Path(tmp_path / "token.json"),
        openai_model="test-model",
    )

    _backfill(_Args(), settings)
    out = capsys.readouterr().out

    assert "[sponsored link stored]" in out
    assert "alpha123" not in out
    assert "tracking=one" not in out
    assert "https://at.indeed.com/viewjob?jk=bitpanda456" in out


def test_screen_command_and_eligible_only_export(tmp_path, capsys) -> None:
    settings = Settings(
        database_path=Path(tmp_path / "jobs.db"),
        credentials_path=Path(tmp_path / "credentials.json"),
        token_path=Path(tmp_path / "token.json"),
    )
    repository = JobRepository(settings.database_path)
    try:
        repository.upsert(
            [
                Job(
                    source="indeed",
                    source_job_id="junior-1",
                    title="Junior Backend Engineer",
                    company="Example GmbH",
                    location="Vienna, Austria",
                    snippet="",
                    url="https://example.com/jobs/junior-1",
                    email_message_id="email-1",
                    email_received_at="2026-06-23T08:00:00Z",
                    alert_subject="Jobs",
                ),
                Job(
                    source="indeed",
                    source_job_id="senior-1",
                    title="Senior Backend Engineer",
                    company="Example GmbH",
                    location="Vienna, Austria",
                    snippet="",
                    url="https://example.com/jobs/senior-1",
                    email_message_id="email-2",
                    email_received_at="2026-06-23T08:00:00Z",
                    alert_subject="Jobs",
                ),
            ]
        )
    finally:
        repository.close()

    _screen(settings)
    output_path = tmp_path / "eligible.json"
    export_args = parser().parse_args(
        [
            "export",
            "--format",
            "json",
            "--only",
            "eligible",
            "--output",
            str(output_path),
        ]
    )
    _export(export_args, settings)

    rows = json.loads(output_path.read_text())
    output = capsys.readouterr().out
    assert len(rows) == 1
    assert rows[0]["source_job_id"] == "junior-1"
    assert rows[0]["screening_status"] == "eligible"
    assert rows[0]["rejection_reasons"][0]["code"] == "relevant_technical_role"
    assert "Screened: 2; eligible: 1; review: 0; rejected: 1" in output
    assert "Exported 1 jobs" in output


def test_review_and_collapsed_eligible_exports(tmp_path) -> None:
    settings = Settings(
        database_path=Path(tmp_path / "jobs.db"),
        credentials_path=Path(tmp_path / "credentials.json"),
        token_path=Path(tmp_path / "token.json"),
    )
    repository = JobRepository(settings.database_path)
    try:
        repository.upsert(
            [
                Job(
                    source="linkedin",
                    source_job_id="eligible-1",
                    title="Junior Backend Engineer",
                    company="Example GmbH",
                    location="Vienna, Austria",
                    snippet="",
                    url="https://www.linkedin.com/jobs/view/eligible-1/",
                    email_message_id="email-1",
                    email_received_at="2026-06-23T08:00:00Z",
                    alert_subject="Jobs",
                ),
                Job(
                    source="indeed",
                    source_job_id="eligible-2",
                    title="Junior Backend Engineer",
                    company="Example GmbH",
                    location="Vienna, Austria",
                    snippet="Longer source description.",
                    url="https://at.indeed.com/viewjob?jk=eligible-2",
                    email_message_id="email-2",
                    email_received_at="2026-06-24T08:00:00Z",
                    alert_subject="Jobs",
                ),
                Job(
                    source="linkedin",
                    source_job_id="review-1",
                    title="Werkstudent:in Communications",
                    company="Example GmbH",
                    location="Vienna, Austria",
                    snippet="",
                    url="https://www.linkedin.com/jobs/view/review-1/",
                    email_message_id="email-3",
                    email_received_at="2026-06-23T08:00:00Z",
                    alert_subject="Jobs",
                ),
            ]
        )
        repository.screen_all()
    finally:
        repository.close()

    review_path = tmp_path / "review.json"
    review_args = parser().parse_args(
        [
            "export",
            "--format",
            "json",
            "--only",
            "review",
            "--output",
            str(review_path),
        ]
    )
    _export(review_args, settings)

    eligible_path = tmp_path / "eligible.json"
    eligible_args = parser().parse_args(
        [
            "export",
            "--format",
            "json",
            "--only",
            "eligible",
            "--collapse-semantic",
            "--output",
            str(eligible_path),
        ]
    )
    _export(eligible_args, settings)

    review_rows = json.loads(review_path.read_text())
    eligible_rows = json.loads(eligible_path.read_text())
    assert [row["source_job_id"] for row in review_rows] == ["review-1"]
    assert len(eligible_rows) == 1
    assert eligible_rows[0]["source_job_id"] == "eligible-2"
    assert eligible_rows[0]["semantic_duplicate_count"] == 1
    assert eligible_rows[0]["alternate_source_urls"] == [
        "https://www.linkedin.com/jobs/view/eligible-1/"
    ]


def test_require_description_excludes_unenriched_semantic_groups(tmp_path) -> None:
    settings = Settings(
        database_path=Path(tmp_path / "jobs.db"),
        credentials_path=Path(tmp_path / "credentials.json"),
        token_path=Path(tmp_path / "token.json"),
    )
    repository = JobRepository(settings.database_path)
    try:
        described = Job(
            source="indeed",
            source_job_id="described",
            title="Junior Backend Engineer",
            company="Described GmbH",
            location="Vienna, Austria",
            snippet="",
            url="https://at.indeed.com/viewjob?jk=described",
            email_message_id="email-1",
            email_received_at="2026-06-23T08:00:00Z",
            alert_subject="Jobs",
        )
        missing = Job(
            source="linkedin",
            source_job_id="missing",
            title="Junior Data Engineer",
            company="Missing GmbH",
            location="Vienna, Austria",
            snippet="",
            url="https://www.linkedin.com/jobs/view/missing/",
            email_message_id="email-2",
            email_received_at="2026-06-23T08:00:00Z",
            alert_subject="Jobs",
        )
        described_duplicate = Job(
            source="linkedin",
            source_job_id="described-duplicate",
            title="Junior Backend Engineer",
            company="Described GmbH",
            location="Vienna, Austria",
            snippet="",
            url="https://www.linkedin.com/jobs/view/described-duplicate/",
            email_message_id="email-3",
            email_received_at="2026-06-22T08:00:00Z",
            alert_subject="Jobs",
        )
        repository.upsert([described, described_duplicate, missing])
        repository.connection.execute(
            "UPDATE jobs SET screening_status = 'eligible'"
        )
        repository.update_enrichment(
            described.dedupe_key,
            {
                "description": "Full description",
                "description_language": "en",
                "fetched_at": "2026-06-23T09:00:00+00:00",
                "fetch_status": "success",
                "fetch_error": "",
                "final_url": described.url,
                "description_hash": "hash",
            },
        )
    finally:
        repository.close()

    output_path = tmp_path / "described.json"
    args = parser().parse_args(
        [
            "export",
            "--format",
            "json",
            "--only",
            "eligible",
            "--collapse-semantic",
            "--require-description",
            "--output",
            str(output_path),
        ]
    )
    _export(args, settings)

    rows = json.loads(output_path.read_text())
    assert [row["source_job_id"] for row in rows] == ["described"]
    assert rows[0]["semantic_duplicate_count"] == 1
    assert rows[0]["alternate_source_urls"] == [
        "https://www.linkedin.com/jobs/view/described-duplicate/"
    ]


def test_enrich_command_reports_all_status_totals(tmp_path, capsys) -> None:
    settings = Settings(
        database_path=Path(tmp_path / "jobs.db"),
        credentials_path=Path(tmp_path / "credentials.json"),
        token_path=Path(tmp_path / "token.json"),
        enrichment_rate_limit_seconds=0,
    )
    args = parser().parse_args(["enrich", "--only", "eligible"])

    _enrich(args, settings)

    assert (
        "Enrichment: success=0; snippet_only=0; expired=0; "
        "blocked=0; empty=0; failed=0"
        in capsys.readouterr().out
    )


def test_enrichment_report_lists_canonical_status_and_http_details(
    tmp_path, capsys
) -> None:
    settings = Settings(
        database_path=Path(tmp_path / "jobs.db"),
        credentials_path=Path(tmp_path / "credentials.json"),
        token_path=Path(tmp_path / "token.json"),
    )
    repository = JobRepository(settings.database_path)
    try:
        item = Job(
            source="indeed",
            source_job_id="report",
            title="Junior Backend Engineer",
            company="Example GmbH",
            location="Vienna, Austria",
            snippet="Email snippet",
            url="https://at.indeed.com/viewjob?jk=report",
            email_message_id="email-report",
            email_received_at="2026-06-23T08:00:00Z",
            alert_subject="Jobs",
        )
        repository.upsert([item])
        repository.connection.execute(
            "UPDATE jobs SET screening_status = 'eligible'"
        )
        repository.update_enrichment(
            item.dedupe_key,
            {
                "description": "Email snippet",
                "description_language": "en",
                "fetched_at": "2026-06-23T09:00:00+00:00",
                "fetch_status": "snippet_only",
                "fetch_error": "HTTP 403",
                "final_url": item.url,
                "description_hash": "hash",
                "description_confidence": "low",
                "http_status": 403,
            },
        )
    finally:
        repository.close()

    args = parser().parse_args(["enrichment-report", "--only", "eligible"])
    _enrichment_report(args, settings)
    output = capsys.readouterr().out

    assert "source\ttitle\tfetch_status\tfetch_error\thttp_status\tfinal_url" in output
    assert (
        "indeed\tJunior Backend Engineer\tsnippet_only\tHTTP 403\t403\t"
        "https://at.indeed.com/viewjob?jk=report"
    ) in output


def test_score_dry_run_does_not_require_api_key(tmp_path, capsys) -> None:
    profile_path = tmp_path / "candidate.yaml"
    profile_path.write_text("name: Test Candidate\n")
    settings = Settings(
        database_path=Path(tmp_path / "jobs.db"),
        credentials_path=Path(tmp_path / "credentials.json"),
        token_path=Path(tmp_path / "token.json"),
        candidate_profile_path=profile_path,
        openai_api_key="",
        openai_model="test-model",
    )
    repository = JobRepository(settings.database_path)
    try:
        item = Job(
            source="linkedin",
            source_job_id="score-dry-run",
            title="Junior Backend Engineer",
            company="Example GmbH",
            location="Vienna, Austria",
            snippet="",
            url="https://www.linkedin.com/jobs/view/score-dry-run/",
            email_message_id="email-score",
            email_received_at="2026-06-23T08:00:00Z",
            alert_subject="Jobs",
        )
        repository.upsert([item])
        repository.connection.execute(
            """
            UPDATE jobs
            SET screening_status = 'eligible', fetch_status = 'success',
                description = 'Python backend vacancy',
                description_hash = 'description-hash',
                description_language = 'en',
                description_confidence = 'high'
            """
        )
        repository.connection.commit()
    finally:
        repository.close()

    args = parser().parse_args(
        ["score", "--only", "eligible", "--limit", "3", "--dry-run"]
    )
    _score(args, settings)
    output = capsys.readouterr().out

    assert "Junior Backend Engineer" in output
    assert "cache=miss" in output
    assert "Dry run candidates: 1" in output


def test_application_report_exports_json_packages(tmp_path) -> None:
    settings = Settings(
        database_path=Path(tmp_path / "jobs.db"),
        credentials_path=Path(tmp_path / "credentials.json"),
        token_path=Path(tmp_path / "token.json"),
        openai_model="test-model",
    )
    repository = JobRepository(settings.database_path)
    try:
        item = Job(
            source="linkedin",
            source_job_id="package",
            title="Junior Backend Engineer",
            company="Example GmbH",
            location="Vienna, Austria",
            snippet="",
            url="https://www.linkedin.com/jobs/view/package/",
            email_message_id="email-package",
            email_received_at="2026-06-23T08:00:00Z",
            alert_subject="Jobs",
        )
        repository.upsert([item])
        description = "Current backend vacancy description"
        repository.connection.execute(
            """
            UPDATE jobs
            SET screening_status = 'eligible', fetch_status = 'success',
                description = ?, description_hash = ?,
                description_confidence = 'high'
            WHERE dedupe_key = ?
            """,
            (description, description_hash(description), item.dedupe_key),
        )
        repository.connection.commit()
        score_key = "score-key"
        repository.save_job_score(
            {
                "score_cache_key": score_key,
                "semantic_key": item.semantic_key,
                "canonical_dedupe_key": item.dedupe_key,
                "candidate_profile_hash": candidate_profile_hash(
                    load_candidate_profile(Path("data/candidate_profile.yaml"))
                ),
                "description_hash": description_hash(description),
                "prompt_version": SCORE_PROMPT_VERSION,
                "model": "test-model",
                "provisional": 0,
                "result_json": json.dumps(
                    {
                        "match_score": 80,
                        "score_confidence": "high",
                        "recommendation": "apply",
                        "matching_strengths": ["Python"],
                        "missing_requirements": [],
                        "risk_flags": [],
                        "role_summary": "Backend role",
                        "provisional": False,
                    }
                ),
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "estimated_cost_usd": 0.001,
                "response_id": "resp_test",
                "score_status": "success",
                "score_error": "",
                "created_at": "2026-06-23T10:00:00+00:00",
            }
        )
        repository.save_application_draft(
            {
                "draft_cache_key": "draft-key",
                "score_cache_key": score_key,
                "semantic_key": item.semantic_key,
                "canonical_dedupe_key": item.dedupe_key,
                "prompt_version": DRAFT_PROMPT_VERSION,
                "model": "test-model",
                "result_json": json.dumps(
                    {
                        "cover_letter_language": "en",
                        "cover_letter": "Application text",
                        "recruiter_message": "Recruiter text",
                    }
                ),
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0,
                "response_id": "",
                "draft_status": "ready",
                "draft_error": "",
                "rewrite_attempted": 0,
                "created_at": "2026-06-23T10:01:00+00:00",
            }
        )
    finally:
        repository.close()

    output_path = tmp_path / "packages.json"
    args = parser().parse_args(
        [
            "application-report",
            "--format",
            "json",
            "--output",
            str(output_path),
        ]
    )
    _application_report(args, settings)

    rows = json.loads(output_path.read_text())
    assert len(rows) == 1
    assert rows[0]["match_score"] == 80
    assert rows[0]["cover_letter"] == "Application text"
    assert rows[0]["model"] == "test-model"
