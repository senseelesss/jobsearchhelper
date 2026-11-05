from dataclasses import replace
import pytest

from job_radar.ai.provider import ScoreProviderResult, TokenUsage
from job_radar.application_models import JobScore
from job_radar.daily import daily_cycle
from job_radar.searches import validate_search_url
from job_radar.sources.base import CollectionBatch, SourceVacancy
from job_radar.storage import JobRepository
from job_radar.work_hours import assess_hours


def job(index=1, **changes):
    return replace(
        SourceVacancy(
            "linkedin",
            str(index),
            f"Junior Python Developer {index}",
            "Example GmbH",
            "Vienna, Austria",
            "Python APIs",
            f"https://www.linkedin.com/jobs/view/{index}/",
            "2026-11-05T12:00:00+00:00",
            "linkedin-public-search-v1",
        ),
        **changes,
    )


SEARCH = validate_search_url("https://www.linkedin.com/jobs/search/?keywords=python", "python")


class Telegram:
    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    def send_message(self, chat_id, text, reply_markup=None):
        self.messages.append(text)
        if self.fail:
            raise TimeoutError("private-token")
        return len(self.messages)


class Provider:
    model = "test-model"

    def __init__(self, fail_first=False):
        self.calls = []
        self.fail_first = fail_first

    def score(self, profile, vacancy, provisional):
        self.calls.append(vacancy["dedupe_key"])
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("private-token private-profile")
        return ScoreProviderResult(
            JobScore(
                match_score=82,
                score_confidence="high",
                provisional=provisional,
                recommendation="apply",
                matching_strengths=["Python"],
                missing_requirements=[],
                risk_flags=[],
                role_summary="Python APIs",
            ),
            TokenUsage(100, 50, 150),
            "synthetic-response",
        )


def cycle(tmp_path, jobs=(), **kwargs):
    return daily_cycle(
        tmp_path / "jobs.db",
        [SEARCH],
        lambda url: CollectionBatch("success", url, jobs=tuple(jobs)),
        output=tmp_path / "output",
        **kwargs,
    )


@pytest.mark.parametrize("fail", [False, True])
def test_alert_identity_survives_restart_next_day_and_changed_title(tmp_path, fail):
    telegram = Telegram(fail)
    first = cycle(tmp_path, [job()], telegram=telegram, chat_id="private-chat", day="2026-11-05")
    second = cycle(
        tmp_path,
        [job(title="Junior Python Engineer updated")],
        telegram=telegram,
        chat_id="private-chat",
        day="2026-11-06",
    )
    assert first["newly_relevant"] == 1
    assert second["newly_relevant"] == 0
    assert second["shortlisted"] == 1
    assert len(telegram.messages) == 2  # daily summary still arrives, no repeated vacancy
    assert "jobs/view/1/" not in telegram.messages[1]
    assert "private-token" not in str(first)


def test_unsent_backlog_is_not_marked_delivered(tmp_path):
    telegram = Telegram()
    jobs = [job(i) for i in range(12)]
    first = cycle(tmp_path, jobs, telegram=telegram, chat_id="chat", day="2026-11-05")
    second = cycle(tmp_path, jobs, telegram=telegram, chat_id="chat", day="2026-11-06")
    assert first["alert_items"] == 8
    assert second["newly_relevant"] == second["alert_items"] == 4
    assert second["inserted"] == 0


def test_source_and_bad_email_failures_are_independent_and_recorded(tmp_path, monkeypatch):
    def parse(message):
        if message == "bad":
            raise ValueError("private email")
        from job_radar.models import Job

        return [
            Job(
                "indeed",
                "mail1",
                "Junior Python Developer",
                "Example",
                "Wien",
                "",
                "https://at.indeed.com/viewjob?jk=mail1",
            )
        ]

    monkeypatch.setattr("job_radar.daily.parse_email", parse)
    second = validate_search_url("https://www.karriere.at/jobs/python/wien", "karriere")

    def collect(url):
        if "linkedin" in url:
            raise TimeoutError("private source data")
        return CollectionBatch("success", url, jobs=(job(2),))

    result = daily_cycle(
        tmp_path / "jobs.db",
        [SEARCH, second],
        collect,
        fetch_emails=lambda: iter(["bad", "good"]),
        output=tmp_path / "output",
        telegram=Telegram(),
        chat_id="chat",
    )
    assert result["status"] == "partial"
    assert result["inserted"] == 2
    assert result["emails"] == 1
    assert result["telegram"] == "sent"
    assert "private email" not in str(result)
    assert "private source data" not in str(result)
    with JobRepository(tmp_path / "jobs.db") as repo:
        assert (
            repo.connection.execute(
                "SELECT status, error FROM run_stage_history WHERE stage='python'"
            ).fetchone()[0]
            == "failed"
        )
        assert repo.run_history()[0]["status"] == "partial"


def test_scoring_failures_are_safe_and_unknown_hours_dont_prevent_scoring(tmp_path):
    provider, telegram = Provider(fail_first=True), Telegram()
    first = cycle(
        tmp_path,
        [job(1), job(2)],
        provider=provider,
        profile={},
        score_limit=2,
        telegram=telegram,
        chat_id="chat",
        input_cost_per_million=0.2,
        output_cost_per_million=1.25,
    )
    assert first["eligible_for_scoring"] == 2
    assert first["scoring"]["failed"] == first["scoring"]["scored"] == 1
    assert first["shortlisted"] == 1
    assert first["estimated_cost_usd"] > 0
    assert first["cost_may_exclude_failed_requests"]
    assert "Hours: unknown" in telegram.messages[0]
    with JobRepository(tmp_path / "jobs.db") as repo:
        scores = repo.connection.execute("SELECT score_error FROM job_scores").fetchall()
        assert all("private" not in row[0] for row in scores)
        # Make the failure old enough for the bounded unattended retry.
        repo.connection.execute(
            "UPDATE job_scores SET created_at='2026-01-01T00:00:00+00:00' WHERE score_status='failed'"
        )
        repo.connection.commit()
    retried = cycle(tmp_path, [job(1), job(2)], provider=provider, profile={}, score_limit=1)
    assert retried["scoring"]["scored"] == 1
    assert len(provider.calls) == 3


def test_applied_and_skipped_survive_connections_and_are_not_scored_or_alerted(tmp_path):
    cycle(tmp_path, [job(1), job(2)])
    with JobRepository(tmp_path / "jobs.db") as repo:
        repo.set_application("linkedin:1", "applied", "manual-1")
        repo.set_application("linkedin:2", "skipped", "manual-2")
    provider, telegram = Provider(), Telegram()
    result = cycle(
        tmp_path,
        [job(1), job(2)],
        provider=provider,
        profile={},
        score_limit=2,
        telegram=telegram,
        chat_id="chat",
    )
    assert result["inserted"] == result["eligible_for_scoring"] == result["shortlisted"] == 0
    assert not provider.calls
    assert "jobs/view" not in telegram.messages[0]


@pytest.mark.parametrize(
    "text,status",
    [
        ("Python developer", "unknown"),
        ("Python Teilzeit oder Vollzeit", "part_time_available"),
        ("Python 20 Stunden pro Woche", "part_time_available"),
        ("Python 20–40 Wochenstunden", "unknown"),
        ("Python Vollzeit", "full_time_only"),
    ],
)
def test_hours_are_separate_evidence(text, status):
    assert assess_hours({"title": text})["hours_status"] == status


def test_fatal_processing_error_still_sends_failure_summary(tmp_path, monkeypatch):
    def broken(*args):
        raise RuntimeError("private data")

    monkeypatch.setattr("job_radar.daily.review_rows", broken)
    telegram = Telegram()
    result = cycle(tmp_path, [job()], telegram=telegram, chat_id="chat")
    assert result["status"] == "partial"
    assert "Partial results" in telegram.messages[0]
    assert result["telegram"] == "sent"


def test_unattended_gmail_never_opens_authorization_browser(tmp_path, monkeypatch):
    from job_radar.gmail import _credentials

    with pytest.raises(RuntimeError, match="reauthorization"):
        _credentials(tmp_path / "client.json", tmp_path / "token.json", interactive=False)


def test_old_daily_payload_blocks_duplicate_after_upgrade(tmp_path):
    cycle(tmp_path, [job()])
    import hashlib
    import json

    chat_hash = hashlib.sha256(b"chat").hexdigest()[:16]
    with JobRepository(tmp_path / "jobs.db") as repo:
        repo.connection.execute(
            "INSERT INTO notification_outbox VALUES (?, ?, 'sent', 1, '')",
            (f"daily:2026-11-04:{chat_hash}", json.dumps({"text": f"Old digest\n{job().url}"})),
        )
        repo.connection.commit()
    result = cycle(tmp_path, [job()], telegram=Telegram(), chat_id="chat")
    assert result["newly_relevant"] == 0


def test_definitive_telegram_rejection_can_retry_immutable_payload(tmp_path):
    from job_radar.telegram import TelegramRejectedRequest

    class Rejected:
        def send_message(self, *args):
            raise TelegramRejectedRequest("HTTP 429")

    first = cycle(tmp_path, [job()], telegram=Rejected(), chat_id="chat", day="2026-11-05")
    assert first["telegram"] == "failed"
    telegram = Telegram()
    second = cycle(tmp_path, [], telegram=telegram, chat_id="chat", day="2026-11-05")
    assert second["telegram_sent_this_run"]
    assert job().url in telegram.messages[0]


def test_interrupted_run_and_backup_failure_are_visible_in_history(tmp_path, monkeypatch):
    with JobRepository(tmp_path / "jobs.db") as repo:
        old = repo.start_run(10, False, True)

    def broken(*args):
        raise OSError("private filesystem details")

    monkeypatch.setattr("job_radar.backups.backup_database", broken)
    result = cycle(tmp_path, [job()])
    assert result["status"] == "partial"
    with JobRepository(tmp_path / "jobs.db") as repo:
        runs = repo.run_history()
        assert runs[0]["status"] == "partial"
        assert runs[1]["id"] == old
        assert runs[1]["status"] == "interrupted"
        assert "Backup: OSError" in runs[0]["error"]
