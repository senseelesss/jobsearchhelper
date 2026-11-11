from pathlib import Path

import pytest

from job_radar.ai.provider import (
    DraftProviderResult,
    ScoreProviderResult,
    TokenUsage,
)
from job_radar.application_models import ApplicationDraftContent, JobScore
from job_radar.candidate import load_candidate_profile
from job_radar.http import HttpResponse
from job_radar.models import EmailMessage, Job
from job_radar.runner import (
    RunAlreadyActiveError,
    RunDependencies,
    RunLock,
    RunOptions,
    run_pipeline,
)
from job_radar.storage import JobRepository

FIXTURES = Path(__file__).parent / "fixtures"


def _cover_letter(company: str, title: str) -> str:
    words = (
        f"I am applying for the {title} role at {company}. "
        "My verified experience covers Python, APIs, integrations, ETL workflows, "
        "backend features, CRM migrations, validation, Docker, Git, and SQL. "
    ).split()
    filler = (
        "I can use this practical background to clarify requirements, map data, "
        "integrate services, test behavior, and deliver maintainable solutions. "
    ).split()
    while len(words) < 190:
        words.extend(filler)
    return " ".join(words[:200])


class FakeProvider:
    model = "test-model"

    def __init__(self) -> None:
        self.score_calls: list[str] = []
        self.draft_calls: list[str] = []

    def score(self, profile, vacancy, provisional) -> ScoreProviderResult:
        del profile, provisional
        self.score_calls.append(str(vacancy["semantic_key"]))
        score = JobScore(
            match_score=81,
            score_confidence="high",
            provisional=False,
            recommendation="apply",
            matching_strengths=["Python and API integration experience"],
            missing_requirements=["No stated cloud platform experience"],
            risk_flags=[],
            role_summary="Backend integration role.",
        )
        return ScoreProviderResult(
            score, TokenUsage(1000, 500, 1500), "score-response"
        )

    def draft(self, profile, vacancy, score) -> DraftProviderResult:
        del profile, score
        self.draft_calls.append(str(vacancy["semantic_key"]))
        draft = ApplicationDraftContent(
            cover_letter_language="en",
            cover_letter=_cover_letter(
                str(vacancy["company"]), str(vacancy["title"])
            ),
            recruiter_message="Vienna-based backend and automation candidate.",
        )
        return DraftProviderResult(
            draft, TokenUsage(400, 300, 700), "draft-response"
        )

    def rewrite_draft(self, profile, vacancy, score, draft) -> DraftProviderResult:
        del profile, vacancy, score
        return DraftProviderResult(
            draft, TokenUsage(), "rewrite-response"
        )


class SelectiveProvider(FakeProvider):
    fail_broken = True

    def score(self, profile, vacancy, provisional) -> ScoreProviderResult:
        if self.fail_broken and vacancy["company"] == "Broken GmbH":
            raise RuntimeError("mock provider failure")
        return super().score(profile, vacancy, provisional)


class FlakyDraftProvider(FakeProvider):
    fail_draft = True

    def draft(self, profile, vacancy, score) -> DraftProviderResult:
        if self.fail_draft:
            raise RuntimeError("mock draft failure")
        return super().draft(profile, vacancy, score)


class FakeHttpClient:
    def __init__(self, responses: dict[str, HttpResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str) -> HttpResponse:
        self.calls.append(url)
        return self.responses[url]


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, chat_id, text, reply_markup) -> str:
        del chat_id, reply_markup
        self.messages.append(text)
        return str(len(self.messages))


class FlakyTelegram(FakeTelegram):
    fail = True

    def send_message(self, chat_id, text, reply_markup) -> str:
        if self.fail:
            raise RuntimeError("mock telegram failure")
        return super().send_message(chat_id, text, reply_markup)


def _message(message_id: str) -> EmailMessage:
    return EmailMessage(
        message_id=message_id,
        received_at="2026-06-23T08:00:00Z",
        sender="alerts@example.com",
        subject="Jobs",
        text="",
        html="",
    )


def _job(message_id: str, snippet: str = "Initial snippet") -> Job:
    return Job(
        source="linkedin",
        source_job_id="incremental-1",
        title="Junior Backend Developer",
        company="Example GmbH",
        location="Vienna, Austria",
        snippet=snippet,
        url="https://www.linkedin.com/jobs/view/incremental-1/",
        email_message_id=message_id,
        email_received_at="2026-06-23T08:00:00Z",
        alert_subject="Jobs",
    )


def _indeed_job(message_id: str) -> Job:
    return Job(
        source="indeed",
        source_job_id="broken-2",
        title="Junior Automation Developer",
        company="Broken GmbH",
        location="Vienna, Austria",
        snippet="Automation role",
        url="https://at.indeed.com/viewjob?jk=broken-2",
        email_message_id=message_id,
        email_received_at="2026-06-23T08:00:00Z",
        alert_subject="Jobs",
    )


def test_incremental_run_reuses_ai_cache_and_does_not_resend(
    monkeypatch, tmp_path
) -> None:
    database = tmp_path / "jobs.db"
    current_job = _job("message-1")
    monkeypatch.setattr("job_radar.runner.parse_email", lambda message: [current_job])
    provider = FakeProvider()
    telegram = FakeTelegram()
    url = current_job.url
    http = FakeHttpClient(
        {
            url: HttpResponse(
                200,
                (FIXTURES / "linkedin_vacancy.html").read_text(),
                url,
            )
        }
    )
    dependencies = RunDependencies(
        fetch_messages=lambda days: [_message(f"message-{days}")],
        http_client=http,
        profile=load_candidate_profile(Path("candidate_profile.example.yaml")),
        ai_provider=provider,
        telegram_transport=telegram,
        telegram_chat_id="test-chat",
        input_cost_per_million=1,
        output_cost_per_million=2,
    )

    first = run_pipeline(database, RunOptions(days=2), dependencies)
    current_job = _job("message-2")
    second = run_pipeline(database, RunOptions(days=2), dependencies)
    current_job = _job("message-3", "Changed email snippet")
    third = run_pipeline(database, RunOptions(days=2), dependencies)

    with JobRepository(database) as repository:
        history = repository.run_history()
        deliveries = repository.connection.execute(
            "SELECT COUNT(*) FROM telegram_deliveries WHERE status = 'delivered'"
        ).fetchone()[0]

    assert first.counts["inserted"] == 1
    assert first.counts["scored"] == 1
    assert first.counts["telegram_sent"] == 1
    assert second.counts["unchanged"] == 1
    assert second.counts["scored"] == 0
    assert third.counts["changed"] == 1
    assert third.counts["score_cache_hits"] == 1
    assert len(provider.score_calls) == 1
    assert len(provider.draft_calls) == 1
    assert len(telegram.messages) == 1
    assert deliveries == 1
    assert len(history) == 3
    assert all(row["status"] == "success" for row in history)


def test_dry_run_only_records_history(monkeypatch, tmp_path) -> None:
    database = tmp_path / "jobs.db"
    item = _job("message-1")
    monkeypatch.setattr("job_radar.runner.parse_email", lambda message: [item])
    dependencies = RunDependencies(
        fetch_messages=lambda days: [_message(str(days))],
        http_client=FakeHttpClient({}),
        profile={},
    )

    report = run_pipeline(database, RunOptions(dry_run=True), dependencies)

    with JobRepository(database) as repository:
        assert repository.all() == []
        assert repository.run_history()[0]["dry_run"] == 1
    assert report.counts["inserted"] == 1
    assert report.counts["scored"] == 0


def test_one_scoring_failure_does_not_stop_other_vacancies(
    monkeypatch, tmp_path
) -> None:
    database = tmp_path / "jobs.db"
    working = _job("message-1")
    broken = _indeed_job("message-2")
    monkeypatch.setattr(
        "job_radar.runner.parse_email",
        lambda message: [working] if message.message_id == "message-1" else [broken],
    )
    telegram = FakeTelegram()
    provider = SelectiveProvider()
    dependencies = RunDependencies(
        fetch_messages=lambda days: [_message("message-1"), _message("message-2")],
        http_client=FakeHttpClient(
            {
                working.url: HttpResponse(
                    200,
                    (FIXTURES / "linkedin_vacancy.html").read_text(),
                    working.url,
                ),
                broken.url: HttpResponse(
                    200,
                    (FIXTURES / "indeed_vacancy.html").read_text(),
                    broken.url,
                ),
            }
        ),
        profile=load_candidate_profile(Path("candidate_profile.example.yaml")),
        ai_provider=provider,
        telegram_transport=telegram,
        telegram_chat_id="test-chat",
    )

    report = run_pipeline(database, RunOptions(), dependencies)
    provider.fail_broken = False
    retried = run_pipeline(database, RunOptions(), dependencies)

    with JobRepository(database) as repository:
        failed = repository.connection.execute(
            """
            SELECT error FROM run_item_status
            WHERE run_id = ? AND stage = 'scoring' AND status = 'failed'
            """,
            (report.run_id,),
        ).fetchone()
    assert report.status == "partial"
    assert report.counts["scored"] == 1
    assert report.counts["telegram_sent"] == 1
    assert failed is not None
    assert failed["error"] == "RuntimeError"
    assert retried.counts["unchanged"] == 2
    assert retried.counts["scored"] == 1


def test_unchanged_vacancy_retries_failed_draft_and_telegram(
    monkeypatch, tmp_path
) -> None:
    database = tmp_path / "jobs.db"
    item = _job("message-1")
    monkeypatch.setattr("job_radar.runner.parse_email", lambda message: [item])
    provider = FlakyDraftProvider()
    telegram = FlakyTelegram()
    dependencies = RunDependencies(
        fetch_messages=lambda days: [_message(str(days))],
        http_client=FakeHttpClient(
            {
                item.url: HttpResponse(
                    200,
                    (FIXTURES / "linkedin_vacancy.html").read_text(),
                    item.url,
                )
            }
        ),
        profile=load_candidate_profile(Path("candidate_profile.example.yaml")),
        ai_provider=provider,
        telegram_transport=telegram,
        telegram_chat_id="test-chat",
    )

    first = run_pipeline(database, RunOptions(), dependencies)
    provider.fail_draft = False
    telegram.fail = False
    second = run_pipeline(database, RunOptions(), dependencies)

    assert first.counts["drafts"] == 0
    assert first.status == "partial"
    assert second.counts["unchanged"] == 1
    assert second.counts["score_cache_hits"] == 1
    assert second.counts["drafts"] == 1
    assert second.counts["telegram_sent"] == 1


def test_unchanged_vacancy_retries_failed_telegram_delivery(
    monkeypatch, tmp_path
) -> None:
    database = tmp_path / "jobs.db"
    item = _job("message-1")
    monkeypatch.setattr("job_radar.runner.parse_email", lambda message: [item])
    telegram = FlakyTelegram()
    dependencies = RunDependencies(
        fetch_messages=lambda days: [_message(str(days))],
        http_client=FakeHttpClient(
            {
                item.url: HttpResponse(
                    200,
                    (FIXTURES / "linkedin_vacancy.html").read_text(),
                    item.url,
                )
            }
        ),
        profile=load_candidate_profile(Path("candidate_profile.example.yaml")),
        ai_provider=FakeProvider(),
        telegram_transport=telegram,
        telegram_chat_id="test-chat",
    )

    first = run_pipeline(database, RunOptions(), dependencies)
    telegram.fail = False
    second = run_pipeline(database, RunOptions(), dependencies)

    assert first.counts["telegram_sent"] == 0
    assert first.status == "partial"
    assert second.counts["unchanged"] == 1
    assert second.counts["telegram_sent"] == 1


def test_unchanged_vacancy_retries_incomplete_enrichment(
    monkeypatch, tmp_path
) -> None:
    database = tmp_path / "jobs.db"
    item = _job("message-1", snippet="")
    monkeypatch.setattr("job_radar.runner.parse_email", lambda message: [item])
    client = FakeHttpClient(
        {
            item.url: HttpResponse(
                200,
                (FIXTURES / "vacancy_blocked.html").read_text(),
                item.url,
            )
        }
    )
    dependencies = RunDependencies(
        fetch_messages=lambda days: [_message(str(days))],
        http_client=client,
        profile=load_candidate_profile(Path("candidate_profile.example.yaml")),
        ai_provider=FakeProvider(),
        telegram_transport=FakeTelegram(),
        telegram_chat_id="test-chat",
    )

    first = run_pipeline(database, RunOptions(), dependencies)
    client.responses[item.url] = HttpResponse(
        200,
        (FIXTURES / "linkedin_vacancy.html").read_text(),
        item.url,
    )
    second = run_pipeline(database, RunOptions(), dependencies)

    with JobRepository(database) as repository:
        row = repository.all()[0]
    assert first.status == "partial"
    assert second.counts["unchanged"] == 1
    assert client.calls == [item.url, item.url]
    assert row["fetch_status"] == "success"


def test_run_lock_rejects_second_process(tmp_path) -> None:
    lock_path = tmp_path / "jobs.db.run.lock"

    with RunLock(lock_path):
        with pytest.raises(RunAlreadyActiveError):
            with RunLock(lock_path):
                pass
