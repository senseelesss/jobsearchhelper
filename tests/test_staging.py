from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from job_radar.candidate import candidate_profile_hash
from job_radar.cli import _daily, parser
from job_radar.config import Settings
from job_radar.daily import daily_cycle, review_rows
from job_radar.enrichment import enrich_jobs
from job_radar.http import HttpResponse
from job_radar.matching import score_jobs, scoring_plan
from job_radar.models import Job
from job_radar.searches import load_searches
from job_radar.storage import JobRepository
from job_radar.work_hours import assess_hours
from test_production import Provider, Telegram, job, SEARCH
from job_radar.sources.base import CollectionBatch


def run(tmp_path, items, provider=None, limit=0, **kwargs):
    return daily_cycle(
        tmp_path / "jobs.db",
        [SEARCH],
        lambda url: CollectionBatch("success", url, jobs=tuple(items)),
        provider=provider,
        profile={},
        score_model="test-model",
        score_limit=limit,
        output=tmp_path / "out",
        **kwargs,
    )


@pytest.mark.parametrize("limit", [0, 2, 5])
def test_both_tiers_remain_visible_under_every_budget_and_replays(tmp_path, limit):
    items = [job(i) for i in range(1, 8)]
    provider, telegram = Provider(), Telegram()
    first = run(
        tmp_path, items, provider, limit, telegram=telegram, chat_id="chat", day="2026-11-05"
    )
    assert len(provider.calls) == limit
    assert first["review_tiers"] == {"ai_recommended": limit, "awaiting_ai": 7 - limit}
    assert first["alert_items"] == first["shortlisted"] == 7
    assert first["funnel"]["this_run"]["delivered_items"] == 7
    if limit < 7:
        assert "UNSCORED" in telegram.messages[0]
    if limit:
        assert "Tier A" in telegram.messages[0] and "confidence: low" in telegram.messages[0]
    run(tmp_path, items, provider, 0, telegram=telegram, chat_id="chat", day="2026-11-05")
    second = run(tmp_path, items, provider, 0, telegram=telegram, chat_id="chat", day="2026-11-06")
    assert len(telegram.messages) == 2
    assert (
        second["alert_items"]
        == second["funnel"]["database_totals"]["pending_notification_backlog"]
        == 0
    )
    assert "jobs/view" not in telegram.messages[1]
    assert len(provider.calls) == limit


def test_missing_key_preserves_unscored_but_does_not_promote_failed_or_rejected(tmp_path):
    provider = Provider(fail_first=True)
    run(tmp_path, [job(1), job(2), job(3, title="Senior Python Engineer")], provider, 1)
    telegram = Telegram()
    result = run(tmp_path, [], None, 5, telegram=telegram, chat_id="chat")
    assert result["scoring"]["status"] == "unavailable"
    assert result["review_tiers"] == {"ai_recommended": 0, "awaiting_ai": 1}
    assert result["funnel"]["database_totals"]["failed"] == 1
    assert result["funnel"]["database_totals"]["retry_deferred"] == 1
    assert "jobs/view/1/" not in telegram.messages[0]
    assert "jobs/view/2/" in telegram.messages[0]
    assert "jobs/view/3/" not in telegram.messages[0]


def test_budget_zero_preserves_ai_skip_and_failure_exclusions(tmp_path):
    provider = Provider()
    run(tmp_path, [job(1)], provider, 1)
    with JobRepository(tmp_path / "jobs.db") as repo:
        row = repo.connection.execute(
            "SELECT score_cache_key,result_json FROM job_scores"
        ).fetchone()
        content = json.loads(row["result_json"])
        content["recommendation"] = "skip"
        repo.connection.execute("UPDATE job_scores SET result_json=?", (json.dumps(content),))
        repo.connection.commit()
    result = run(tmp_path, [job(2)], provider, 0)
    assert result["review_tiers"] == {"ai_recommended": 0, "awaiting_ai": 1}
    assert result["funnel"]["database_totals"]["current_scored"] == 1


def test_cache_and_cooldown_do_not_take_paid_call_slots(tmp_path):
    provider = Provider(fail_first=True)
    run(tmp_path, [job(1), job(2)], provider, 2)
    result = run(tmp_path, [job(3), job(4)], provider, 2)
    assert result["scoring"]["scored"] == 2
    assert len(provider.calls) == 4
    assert len(set(provider.calls)) == 4
    assert result["scoring"]["cached"] == 1


def test_later_ai_evaluation_does_not_resend_a_tier_b_alert(tmp_path):
    telegram, provider = Telegram(), Provider()
    first = run(tmp_path, [job()], provider, 0, telegram=telegram, chat_id="chat", day="2026-11-05")
    assert first["alert_items"] == 1
    second = run(tmp_path, [], provider, 1, telegram=telegram, chat_id="chat", day="2026-11-06")
    assert second["review_tiers"]["ai_recommended"] == 1
    assert second["alert_items"] == 0
    assert "jobs/view" not in telegram.messages[1]


def test_current_context_finds_cached_score_even_after_another_models_score(tmp_path):
    first = Provider()
    run(tmp_path, [job()], first, 1)
    second = Provider()
    second.model = "other-model"
    with JobRepository(tmp_path / "jobs.db") as repo:
        score_jobs(repo, second, {}, limit=1)
        rows = review_rows(repo, candidate_profile_hash({}), first.model)
        assert rows[0]["score_status"] == "success"
        assert rows[0]["review_tier"] == "A"
        assert (
            review_rows(repo, candidate_profile_hash({"changed": True}), first.model)[0][
                "review_tier"
            ]
            == "B"
        )


class Details:
    def __init__(self, body=None, status=200):
        self.calls = []
        self.body = (
            body
            or '<div class="show-more-less-html__markup">Junior Python APIs. Teilzeit 20 Stunden pro Woche. Build integrations and write tests.</div>'
        )
        self.status = status

    def get(self, url):
        self.calls.append(url)
        return HttpResponse(self.status, self.body, url)


def test_daily_enriches_email_and_direct_jobs_before_scoring_and_reuses_cache(
    tmp_path, monkeypatch
):
    email = Job(
        "linkedin",
        "90",
        "Junior Python Developer mail",
        "Mail GmbH",
        "Wien",
        "Python APIs",
        "https://www.linkedin.com/jobs/view/90/",
    )
    monkeypatch.setattr("job_radar.daily.parse_email", lambda message: [email])
    provider, details = Provider(), Details()
    result = run(
        tmp_path,
        [job(1)],
        provider,
        2,
        fetch_emails=lambda: ["message"],
        enrichment_client=details,
        enrichment_limit=2,
    )
    assert result["enrichment"]["requests"] == 2
    assert result["scoring"]["scored"] == 2
    with JobRepository(tmp_path / "jobs.db") as repo:
        scores = repo.connection.execute("SELECT provisional FROM job_scores").fetchall()
        assert all(not row[0] for row in scores)
        assert all(r["hours_target_fit"] == "confirmed" for r in review_rows(repo))
    again = run(tmp_path, [job(1)], provider, 2, enrichment_client=details, enrichment_limit=2)
    assert again["enrichment"]["requests"] == 0
    assert again["scoring"]["scored"] == 0
    assert len(details.calls) == len(provider.calls) == 2


def test_full_jd_hard_rejection_happens_before_scoring(tmp_path):
    details = Details(
        '<div class="show-more-less-html__markup">Minimum 8 years of professional experience. Build Python APIs.</div>'
    )
    provider = Provider()
    result = run(tmp_path, [job()], provider, 2, enrichment_client=details, enrichment_limit=1)
    assert result["eligible_for_scoring"] == 0
    assert not provider.calls
    assert result["shortlisted"] == 0


def test_detail_failure_keeps_provisional_score_and_does_not_retry_every_run(tmp_path):
    provider, details = Provider(), Details("Access denied", 403)
    first = run(tmp_path, [job()], provider, 1, enrichment_client=details, enrichment_limit=1)
    assert first["scoring"]["scored"] == 1
    with JobRepository(tmp_path / "jobs.db") as repo:
        assert repo.connection.execute("SELECT provisional FROM job_scores").fetchone()[0]
        row = review_rows(repo)[0]
        assert row["fetch_status"] == "snippet_only"
        assert row["score_confidence"] == "low"
    run(tmp_path, [job()], provider, 1, enrichment_client=details, enrichment_limit=1)
    assert len(provider.calls) == len(details.calls) == 1


def test_enrichment_budget_does_not_mark_unattempted_rows_failed(tmp_path):
    run(tmp_path, [job(i) for i in range(3)])
    client = Details()
    with JobRepository(tmp_path / "jobs.db") as repo:
        result = enrich_jobs(repo, client, request_limit=1, max_seconds=120)
        assert result["requests"] == 1
        assert result["deferred"] == 2
        assert sum(r["fetch_status"] == "" for r in repo.all()) == 2


def test_legacy_success_without_confidence_is_not_silently_verified(tmp_path):
    from job_radar.matching import has_full_description

    run(tmp_path, [job()])
    client = Details()
    with JobRepository(tmp_path / "jobs.db") as repo:
        repo.connection.execute(
            "UPDATE jobs SET fetch_status='success',description='Python API work',"
            "description_confidence='',fetched_at=?",
            (datetime.now(timezone.utc).isoformat(),),
        )
        repo.connection.commit()
        assert not has_full_description(repo.all()[0])
        result = enrich_jobs(repo, client, request_limit=1)
        assert result["requests"] == len(client.calls) == 1
        assert has_full_description(repo.all()[0])


def test_fair_plan_prioritizes_new_part_time_and_guarantees_old_backlog(tmp_path):
    run(tmp_path, [job(1), job(2, snippet="Python Teilzeit 20 Stunden pro Woche"), job(3)])
    now = datetime.now(timezone.utc)
    with JobRepository(tmp_path / "jobs.db") as repo:
        repo.connection.execute(
            "UPDATE jobs SET created_at=? WHERE source_job_id='1'",
            ((now - timedelta(days=2)).isoformat(),),
        )
        repo.connection.commit()
        rows = repo.all()
        new_keys = {r["semantic_key"] for r in rows if r["source_job_id"] != "1"}
        plan, _ = scoring_plan(
            repo, {}, "test-model", limit=2, new_semantic_keys=new_keys, fairness_turn=1
        )
        assert [r["source_job_id"] for r in plan] == ["2", "1"]
        plan, _ = scoring_plan(
            repo, {}, "test-model", limit=1, new_semantic_keys=new_keys, fairness_turn=3
        )
        assert plan[0]["source_job_id"] == "1"
        repo.connection.execute(
            "UPDATE jobs SET created_at=? WHERE source_job_id='1'",
            ((now - timedelta(days=8)).isoformat(),),
        )
        repo.connection.commit()
        plan, _ = scoring_plan(
            repo, {}, "test-model", limit=1, new_semantic_keys=new_keys, fairness_turn=1
        )
        assert plan[0]["source_job_id"] == "1"


def test_cli_missing_api_key_does_not_construct_provider_or_hide_review(
    tmp_path, monkeypatch, capsys
):
    profile = tmp_path / "profile.yaml"
    profile.write_text("experience: [verified example]\n")
    searches = tmp_path / "searches.yaml"
    searches.write_text(f"searches:\n  - url: {SEARCH.url}\n")
    settings = Settings(
        tmp_path / "unused.db",
        tmp_path / "client.json",
        tmp_path / "token.json",
        candidate_profile_path=profile,
        openai_api_key="",
        openai_model="test-model",
    )
    args = parser().parse_args(
        [
            "daily",
            "--no-email",
            "--score-limit",
            "2",
            "--enrich-limit",
            "0",
            "--searches",
            str(searches),
            "--database",
            str(tmp_path / "jobs.db"),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    monkeypatch.setattr(
        "job_radar.collection.collect_search",
        lambda *a, **k: CollectionBatch("success", SEARCH.url, jobs=(job(),)),
    )
    with pytest.raises(SystemExit):
        _daily(args, settings)
    report = json.loads((tmp_path / "out/report.json").read_text())
    assert report["review_tiers"]["awaiting_ai"] == 1
    assert report["scoring"]["status"] == "unavailable"


def test_suggested_searches_fit_existing_connector_limits():
    searches = load_searches(Path("deploy/searches.vienna.example.yaml"))
    assert 4 <= len(searches) <= 6
    assert len({s.url for s in searches}) == len(searches)
    assert {s.source for s in searches} == {"linkedin", "karriere"}


@pytest.mark.parametrize(
    "text,fit",
    [
        ("Teilzeit", "unknown"),
        ("Teilzeit 20–25 Std./Woche", "confirmed"),
        ("Python 20 h/Woche", "confirmed"),
        ("Part time 25 hours a week", "confirmed"),
        ("Teilzeit 10 Stunden pro Woche", "outside_target"),
    ],
)
def test_hours_target_requires_explicit_weekly_evidence(text, fit):
    assert assess_hours({"title": text})["hours_target_fit"] == fit
