"""Integrated RC acceptance: real services/SQLite with fake network and AI."""

import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from job_radar.ai.budget import BudgetedProvider, BudgetExceeded, request_ceiling
from job_radar.config import Settings
from job_radar.control.catalog import CATALOG, label
from job_radar.control.jobs import JobQuery
from job_radar.control.state import ControlStore
from job_radar.daily import daily_cycle
from job_radar.enrichment import enrichment_report
from job_radar.enrichment_adapters import adapter_for
from job_radar.enrichment_adapters.common import select_job_document
from job_radar.eligibility import assess_application
from job_radar.ingestion import ingest_observations
from job_radar.operations import health
from job_radar.sources.base import CollectionBatch
from job_radar.storage import JobRepository
from test_control_center import rig as rig, callback, click, command, payload
from test_production import Provider, SEARCH, job

FIXTURES = Path(__file__).parent / "fixtures"
FULL = (FIXTURES / "structured_job.html").read_text()


@pytest.mark.parametrize(
    "title",
    [
        "Dental Assistant",
        "Zahnarztassistenz",
        "Ordinationsassistenz",
        "Office Assistant",
        "Medizinische Assistenz",
    ],
)
def test_non_it_part_time_never_becomes_recommended(tmp_path, title):
    with JobRepository(tmp_path / "jobs.db") as repo:
        ingest_observations(
            repo, [job(title=title, snippet="20 hours/week; use software and Python reports")]
        )
        repo.screen_all()
        assert assess_application(repo.all()[0])["application_eligibility"] == "ineligible"
        store = ControlStore(tmp_path / "jobs.db")
        try:
            assert not JobQuery(repo, store).detail(job().dedupe_key)["recommended"]
        finally:
            store.close()
        report = daily_cycle(tmp_path / "jobs.db", [], lambda _: None, output=tmp_path / "out")
        assert report["shortlisted"] == 0


@pytest.mark.parametrize(
    "title",
    [
        "Dental Software Developer Python",
        "Odoo Python Developer",
        "QA Automation Engineer",
        "Junior Python Developer",
    ],
)
def test_contextual_technical_roles_remain_relevant(title):
    result = assess_application(
        {"title": title, "location": "Vienna", "description": "Python APIs. 20 hours per week."}
    )
    assert result["role_fit"] in {"strong", "relevant", "secondary"}
    assert result["application_eligibility"] != "ineligible"


def test_void_elements_and_script_content_do_not_truncate_or_pollute_jd():
    document = '<div class="show-more-less-html__markup">Build Python APIs.<br/>Requirements: SQL.<img src="x"><p>Responsibilities: tests and integrations.</p><script>secret navigation junk</script></div>'
    result = adapter_for("linkedin").parse(document, 200)
    assert result.status == "success"
    assert "Responsibilities: tests and integrations." in result.description
    assert "secret navigation junk" not in result.description


def test_related_jsonld_job_is_selected_by_identity():
    a = {
        "@type": "JobPosting",
        "title": "Junior Python",
        "url": "https://example.org/jobs/1",
        "description": "<p>Build Python APIs and tests.</p>",
    }
    b = {
        **a,
        "title": "Senior Java",
        "url": "https://example.org/jobs/2",
        "description": "Unrelated job",
    }
    document = '<script type="application/ld+json">' + json.dumps([b, a]) + "</script>"
    result = adapter_for("linkedin").parse(select_job_document(document, a["title"], a["url"]), 200)
    assert result.status == "success" and "Build Python" in result.description
    assert "Unrelated job" not in result.description


def test_generic_footer_does_not_downgrade_meaningful_full_jd():
    doc = '<div class="job_description"><h2>Responsibilities</h2><p>Build Python APIs.</p><h2>Requirements</h2><p>SQL and automated testing.</p><p>Join our talent community</p></div>'
    assert adapter_for("absolventen").parse(doc, 200).status == "success"
    assert (
        adapter_for("absolventen")
        .parse('<div class="job_description">Join our talent community</div>', 200)
        .status
        == "snippet_only"
    )


def test_source_429_cooldown_survives_restart_and_other_sources_continue(tmp_path):
    searches = [
        SEARCH,
        replace(SEARCH, name="second"),
        replace(SEARCH, name="other", source="karriere"),
    ]
    calls = []

    def collect(url):
        calls.append(url)
        return CollectionBatch(
            "rate_limited" if len(calls) == 1 else "empty",
            url,
            http_status=429 if len(calls) == 1 else 200,
        )

    first = daily_cycle(tmp_path / "jobs.db", searches, collect, output=tmp_path / "out")
    assert first["status"] == "partial" and len(calls) == 2
    calls.clear()
    second = daily_cycle(tmp_path / "jobs.db", searches[:2], collect, output=tmp_path / "out")
    assert not calls and all(s["stop"] == "cooldown" for s in second["sources"])
    with JobRepository(tmp_path / "jobs.db") as repo:
        repo.connection.execute("UPDATE source_cooldowns SET next_attempt_at='2000-01-01'")
        repo.connection.commit()
    daily_cycle(
        tmp_path / "jobs.db",
        searches[:1],
        lambda url: calls.append(url) or CollectionBatch("empty", url),
        output=tmp_path / "out",
    )
    assert len(calls) == 1


def test_diagnostics_distinguish_not_attempted_from_retry_scheduled(tmp_path):
    with JobRepository(tmp_path / "jobs.db") as repo:
        ingest_observations(repo, [job()])
        repo.screen_all()
        assert enrichment_report(repo)[0]["attempt_state"] == "NOT_ATTEMPTED"
        repo.record_enrichment_attempt(job().url, "failed", 1, {}, 24)
        assert enrichment_report(repo)[0]["attempt_state"] == "RETRY_SCHEDULED"
        repo.connection.execute("UPDATE enrichment_attempts SET next_retry_at='2000-01-01'")
        repo.connection.commit()
        assert enrichment_report(repo)[0]["attempt_state"] == "RETRY_DUE"


def settings(path):
    return Settings(path, path, path)


def test_budget_reservation_is_atomic_durable_and_survives_failure(tmp_path):
    path = tmp_path / "jobs.db"
    with JobRepository(path):
        pass
    cfg = settings(path)
    ceiling = request_ceiling(cfg)
    provider = Mock(model="test")
    provider.score.side_effect = TimeoutError("do not log")
    wrapped = BudgetedProvider(provider, path, cfg, ceiling, ceiling)
    with pytest.raises(TimeoutError):
        wrapped.score({}, {}, False)
    with pytest.raises(BudgetExceeded):
        wrapped.score({}, {}, False)
    restarted = BudgetedProvider(provider, path, cfg, ceiling * 2, ceiling)
    with pytest.raises(BudgetExceeded):
        restarted.score({}, {}, False)
    assert provider.score.call_count == 1
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT COUNT(*) FROM ai_budget_reservations").fetchone()[0] == 1


def test_budget_exhaustion_stops_existing_score_service_without_fake_failures(tmp_path):
    path = tmp_path / "jobs.db"
    with JobRepository(path) as repo:
        ingest_observations(repo, [job(1), job(2)])
        repo.screen_all()
        from job_radar.matching import score_jobs

        provider = Provider()
        cfg = settings(path)
        ceiling = request_ceiling(cfg)
        guarded = BudgetedProvider(provider, path, cfg, ceiling, ceiling)
        result = score_jobs(repo, guarded, {}, only=None, limit=2)
        assert result.scored == 1 and result.failed == 0 and len(provider.calls) == 1
        assert (
            result.errors
            and repo.connection.execute("SELECT COUNT(*) FROM job_scores").fetchone()[0] == 1
        )


def test_full_search_requires_owner_confirmation_and_rejects_replay(rig, monkeypatch):
    rig.search.config = replace(
        rig.search.config,
        ai_ready=True,
        model="test",
        request_cost_usd=0.01,
        run_budget_usd=0.02,
        day_budget_usd=0.04,
    )
    launch = Mock(return_value={"status": "queued"})
    monkeypatch.setattr(rig.search, "start", launch)
    callback(rig, payload(rig, action="full_search"), user=99)
    assert not launch.called
    command(rig)
    click(rig, "find")
    click(rig, "full_search")
    assert not launch.called and "$0.0200" in rig.transport.messages[-1][0]
    token = click(rig, "confirm_paid")
    callback(rig, token)
    assert launch.call_count == 1 and launch.call_args.kwargs["mode"] == "full"


def test_gateway_rejects_full_search_without_valid_quote(rig):
    assert rig.search.start(mode="full")["status"] == "ai_unavailable"
    rig.search.config = replace(
        rig.search.config,
        ai_ready=True,
        request_cost_usd=0.01,
        run_budget_usd=0.02,
        day_budget_usd=0.04,
    )
    assert rig.search.start(mode="full", confirmation="forged")["status"] == "ai_unavailable"


@pytest.mark.parametrize("status", ["success", "partial", "failed", "timeout", "interrupted"])
def test_completion_notification_is_durable_and_sent_once(rig, status):
    rig.store.completed({"id": "test-run", "status": status, "summary": {"inserted": 2}})
    rig.search_notifications()
    count = len(rig.transport.messages)
    assert count == 1 and rig.t(status) in rig.transport.messages[-1][0]
    rig.search_notifications()
    assert len(rig.transport.messages) == count
    assert rig.store.connection.execute("SELECT status FROM search_events").fetchone()[0] == "sent"


def test_unknown_completion_is_not_blindly_retried(rig, monkeypatch):
    rig.store.completed({"id": "test-run", "status": "success"})
    transport = Mock(side_effect=TimeoutError("private-token"))
    monkeypatch.setattr(rig.transport, "send_message", transport)
    rig.search_notifications()
    rig.search_notifications()
    assert transport.call_count == 1
    assert (
        rig.store.connection.execute("SELECT status FROM search_events").fetchone()[0] == "unknown"
    )


def test_health_is_readonly_and_reports_missing_components(tmp_path, monkeypatch):
    cfg = settings(tmp_path / "missing.db")
    report = health(
        cfg, cfg.database_path, tmp_path / "missing.yaml", tmp_path / "out", require_telegram=True
    )
    assert report["status"] == "failed" and not cfg.database_path.exists()
    assert "telegram_owner_or_token_not_configured" in report["errors"]
    assert "database_missing_run_init_db" in report["errors"]


def test_health_valid_database_reports_actual_run_and_schema(rig):
    cfg = settings(rig.search.config.database)
    result = health(cfg, cfg.database_path, rig.search.config.searches, rig.search.config.output)
    assert result["status"] == "ok" and result["integrity"] == "ok"
    assert result["last_run"] is None


@pytest.mark.parametrize("language", ["uk", "de", "en"])
def test_added_search_and_completeness_strings_localized(rig, language):
    rig.store.set("language", language)
    command(rig)
    click(rig, "find")
    assert rig.t("full_search") in [
        b["text"] for row in rig.transport.messages[-1][1]["inline_keyboard"] for b in row
    ]
    assert label(language, "INACCESSIBLE") != "INACCESSIBLE"
    assert CATALOG[language]["confirm_paid"]


def test_real_quick_gateway_enriches_fixture_jobs_and_survives_restart(rig, monkeypatch):
    real_popen = subprocess.Popen
    monkeypatch.setenv("PYTHONPATH", str(Path("src").resolve()))
    monkeypatch.setenv(
        "JOB_RADAR_CANDIDATE_PROFILE", str(Path("candidate_profile.example.yaml").resolve())
    )

    def launch(cmd, **kw):
        daily = json.loads(cmd[-1])
        program = (
            "import sys; from pathlib import Path; from unittest.mock import patch; "
            "from job_radar.cli import main; from job_radar.http import HttpResponse; "
            "sys.argv=" + repr(["job-radar", *daily[3:]]) + "; "
            "search=" + repr((FIXTURES / "karriere_search.html").read_text()) + "; "
            "detail=" + repr(FULL) + "; "
            "patch('job_radar.http.HttpClient.get',lambda self,url:HttpResponse(200,search if '/jobs/python/wien' in url else detail,url)).start(); main()"
        )
        cmd[-1] = json.dumps([sys.executable, "-c", program])
        return real_popen(cmd, **kw)

    monkeypatch.setattr(subprocess, "Popen", launch)
    rig.search.config = replace(rig.search.config, transport="http")
    command(rig)
    click(rig, "find")
    click(rig, "start_search")
    rig.search.children[0].wait(timeout=15)
    state = rig.search.status()
    assert state["summary"]["inserted"] == 2
    assert state["summary"]["enrichment"]["requests"] == 3
    rig.search_notifications()
    assert rig.store.connection.execute("SELECT status FROM search_events").fetchone()[0] == "sent"
    rig.detail("karriere:1234567")
    click(rig, "save")
    click(rig, "apply")
    with JobRepository(rig.search.config.database) as repo:
        assert repo.application_for("karriere:1234567")["state"] == "applied"
        assert (
            "Responsibilities"
            in next(r for r in repo.all() if r["dedupe_key"] == "karriere:1234567")["description"]
        )
    # Browsing did not create a daily delivery acknowledgement.
    assert (
        rig.repository.connection.execute("SELECT COUNT(*) FROM daily_alert_items").fetchone()[0]
        == 0
    )


def test_opt_in_board_filter_applies_before_item_limit(tmp_path):
    from job_radar.collection import collect_search
    from job_radar.http import HttpResponse
    from job_radar.searches import load_searches

    url = "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true"
    path = tmp_path / "searches.yaml"
    path.write_text(
        f"searches:\n  - url: {url}\n    title_terms: [Python]\n    location_terms: [Vienna]\n"
    )
    search = load_searches(path)[0]
    body = json.loads((FIXTURES / "greenhouse_board.json").read_text())
    target = body["jobs"][0]
    body["jobs"].insert(
        0,
        {
            **target,
            "id": 999,
            "title": "Accountant",
            "absolute_url": "https://job-boards.greenhouse.io/example/jobs/999",
        },
    )
    client = Mock()
    client.metrics = {}
    client.get.return_value = HttpResponse(200, json.dumps(body), url)
    batch = collect_search(client, url, limit=1, include=search.includes)
    assert batch.status == "success" and batch.jobs[0].title == target["title"]
    assert batch.metrics["filtered_out"] == 1


def test_austrian_office_snippet_can_discover_remote_evidence(tmp_path):
    from job_radar.http import HttpResponse

    item = job(title="Junior Python Developer", location="Graz", snippet="Build Python APIs.")
    client = Mock()
    client.metrics = {}
    client.get.return_value = HttpResponse(200, FULL, item.url)
    report = daily_cycle(
        tmp_path / "jobs.db",
        [SEARCH],
        lambda url: CollectionBatch("success", url, jobs=(item,)),
        output=tmp_path / "out",
        enrichment_client=client,
        enrichment_limit=1,
    )
    assert client.get.call_count == 1
    assert report["shortlisted"] == 1
    assert report["jd_completeness"]["FULL"] == 1


def test_reopen_does_not_erase_applied_legacy_delivery_state(tmp_path):
    path = tmp_path / "jobs.db"
    with JobRepository(path) as repo:
        ingest_observations(repo, [job()])
        key = repo.all()[0]["semantic_key"]
        repo.save_telegram_delivery(key, 42, "2026-11-10")
        repo.connection.execute(
            "UPDATE telegram_deliveries SET delivery_status='applied',status='delivered'"
        )
        repo.connection.commit()
    for _ in range(2):
        with JobRepository(path) as repo:
            assert repo.application_for(job().dedupe_key)["state"] == "applied"
            assert repo.telegram_delivery(key)["delivery_status"] == "applied"


def test_lever_documented_supplement_recovers_actual_duties_without_salary_hours_confusion():
    from job_radar.enrichment_adapters.ats import parse_posting
    from job_radar.work_hours import assess_hours

    value = {
        "text": "AI / ML Engineer",
        "description": "<p>Join our AI team.</p>",
        "salaryDescription": "<h2>Aufgaben</h2><p>Build Databricks pipelines.</p><p>Auslastung: 100%</p>",
        "workplaceType": "hybrid",
    }
    parsed = parse_posting(json.dumps(value), 200, "lever")
    assert "Build Databricks pipelines" in parsed.description
    assert parsed.metadata["workplaceType"] == "hybrid"
    assert assess_hours({"description": parsed.description})["hours_fit"] == "confirmed_mismatch"
    assert assess_hours({"description": "Workload: 100% preferred"})["hours_fit"] == "unknown"


def test_source_cooldown_respects_long_retry_after(tmp_path):
    from datetime import datetime, timezone

    daily_cycle(
        tmp_path / "jobs.db",
        [SEARCH],
        lambda url: CollectionBatch("rate_limited", url, http_status=429, retry_after_seconds=7200),
        output=tmp_path / "out",
    )
    with JobRepository(tmp_path / "jobs.db") as repo:
        until = datetime.fromisoformat(repo.source_cooldown(SEARCH.source))
        assert (until - datetime.now(timezone.utc)).total_seconds() > 7100
