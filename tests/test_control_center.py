import fcntl
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from unittest.mock import Mock

import pytest
import yaml

from job_radar.control.catalog import CATALOG, LABELS, label, tr
from job_radar.control.jobs import JobQuery
from job_radar.control.notifications import preferences
from job_radar.control.search import SearchConfig, SearchService
from job_radar.control.state import ControlStore
from job_radar.control.ui import ControlCenter, clip, public_url
from job_radar.control.worker import supervise
from job_radar.models import Job
from job_radar.storage import JobRepository
from job_radar.telegram import TelegramRejectedRequest


class Telegram:
    def __init__(self):
        self.messages = []
        self.answers = []
        self.edits = 0

    def send_message(self, chat, text, markup=None):
        self.messages.append((text, markup))
        return len(self.messages)

    def edit_message(self, chat, message_id, text, markup=None):
        self.edits += 1
        self.messages.append((text, markup))

    def answer_callback(self, query, text=""):
        self.answers.append((query, text))


@pytest.fixture
def rig(tmp_path):
    database = tmp_path / "jobs.db"
    repository = JobRepository(database)
    repository.upsert(
        [
            Job(
                source="new-source" if i == 7 else "linkedin",
                source_job_id=str(i),
                title=f"Junior Python Developer {i}",
                company=f"Company {i}",
                location="Vienna (Hybrid)",
                snippet="Python APIs; 20 hours per week",
                url=f"https://example.org/jobs/{i}",
            )
            for i in range(12)
        ]
    )
    store = ControlStore(database)
    searches = tmp_path / "searches.yaml"
    searches.write_text(
        yaml.safe_dump(
            {"searches": [{"name": "backend", "url": "https://www.karriere.at/jobs/python/wien"}]}
        )
    )
    search = SearchService(store, SearchConfig(database, searches, tmp_path / "output", cooldown=0))
    jobs = JobQuery(repository, store)
    transport = Telegram()
    center = ControlCenter(jobs, search, transport, 42, "42")
    yield center
    store.close()
    repository.close()


def command(rig, text="/start", user=42, chat=42):
    rig.handle(
        {
            "message": {
                "message_id": 1,
                "text": text,
                "from": {"id": user},
                "chat": {"id": chat, "type": "private"},
            }
        }
    )


def callback(rig, data, user=42, chat=42):
    rig.handle(
        {
            "callback_query": {
                "id": "test",
                "data": data,
                "from": {"id": user},
                "message": {"message_id": 1, "chat": {"id": chat, "type": "private"}},
            }
        }
    )


def click(rig, name):
    text = rig.t(name)
    for row in rig.transport.messages[-1][1]["inline_keyboard"]:
        for button in row:
            if button["text"] == text:
                callback(rig, button["callback_data"])
                return button["callback_data"]
    raise AssertionError(f"Missing {name}: {rig.transport.messages[-1]}")


def payload(rig, **p):
    return rig.store.token(p)


@pytest.mark.parametrize("command_name", ["/start", "/menu", "/cancel", "/help"])
def test_commands(rig, command_name):
    command(rig, command_name)
    assert rig.transport.messages
    assert "cc:" in str(rig.transport.messages[-1][1])


@pytest.mark.parametrize("user,chat", [(43, 42), (42, 43), (None, 42)])
def test_authorization_before_private_reads(rig, user, chat, monkeypatch):
    monkeypatch.setattr(rig.jobs, "statistics", Mock(side_effect=AssertionError("private read")))
    command(rig, user=user, chat=chat)
    callback(rig, payload(rig, action="home"), user=user, chat=chat)
    assert not rig.transport.messages
    assert rig.transport.answers[-1][1] == tr("uk", "denied")


def test_legacy_callbacks_also_owner_guarded(rig):
    callback(rig, "applyjob:linkedin:0", user=43)
    assert rig.repository.application_for("linkedin:0")["state"] == "not_applied"
    callback(rig, "applyjob:linkedin:0")
    # Unverifiable old payloads must not mutate a possibly replaced native identity.
    assert rig.repository.application_for("linkedin:0")["state"] == "not_applied"
    rig.detail("linkedin:0")
    click(rig, "apply")
    assert rig.repository.application_for("linkedin:0")["state"] == "applied"


@pytest.mark.parametrize("language", ["uk", "de", "en"])
def test_catalog_completeness_and_switch(rig, language):
    assert set(CATALOG[language]) == set(CATALOG["en"])
    for key in CATALOG["en"]:
        assert CATALOG[language][key]
    assert all(len(values) == 3 and all(values) for values in LABELS.values())
    callback(rig, payload(rig, action="language", value=language))
    assert rig.store.get("language") == language
    assert tr(language, "settings") in rig.transport.messages[-1][0]
    command(rig)
    assert tr(language, "unreviewed") in rig.transport.messages[-1][0]
    assert tr(language, "save") != "save"


def test_fallback():
    assert tr("zz", "menu") == tr("uk", "menu")
    assert tr("de", "future") == "future"
    assert label("uk", "future-source") == "future-source"


def test_browse_detail_save_applied_and_statistics(rig):
    command(rig)
    click(rig, "recommendations")
    first = rig.transport.messages[-1][1]["inline_keyboard"][0][0]
    callback(rig, first["callback_data"])
    assert "Junior Python" in rig.transport.messages[-1][0]
    saved = click(rig, "save")
    key = rig.store.resolve(saved)["key"]
    assert rig.repository.application_for(key)["state"] == "saved"
    callback(rig, saved)  # exact replay creates no duplicate event
    assert (
        rig.repository.connection.execute("SELECT COUNT(*) FROM application_events").fetchone()[0]
        == 1
    )
    click(rig, "apply")
    assert rig.repository.application_for(key)["state"] == "applied"
    click(rig, "menu")
    click(rig, "applications")
    assert "Junior Python" in rig.transport.messages[-1][0]
    click(rig, "menu")
    click(rig, "statistics")
    assert rig.t("applications") + ": 1" in rig.transport.messages[-1][0]
    assert not rig.repository.connection.execute("SELECT * FROM telegram_deliveries").fetchall()
    assert not rig.repository.connection.execute("SELECT * FROM notification_outbox").fetchall()


@pytest.mark.parametrize(
    "state",
    ["saved", "applied", "interview", "offer", "rejected", "withdrawn", "skipped", "not_applied"],
)
def test_all_existing_application_states(rig, state):
    rig.detail("linkedin:0")
    buttons = rig.transport.messages[-1][1]["inline_keyboard"]
    data = next(
        b["callback_data"]
        for row in buttons
        for b in row
        if b.get("callback_data")
        and (rig.store.resolve(b["callback_data"]) or {}).get("state") == state
    )
    callback(rig, data)
    assert rig.repository.application_for("linkedin:0")["state"] == state


def test_stale_revision_cannot_overwrite_newer_action(rig):
    rig.detail("linkedin:0")
    old = next(
        b["callback_data"]
        for row in rig.transport.messages[-1][1]["inline_keyboard"]
        for b in row
        if b["text"] == rig.t("skip")
    )
    click(rig, "apply")
    callback(rig, old)
    assert rig.repository.application_for("linkedin:0")["state"] == "applied"


def test_changed_identity_and_deleted_job(rig):
    rig.detail("linkedin:0")
    data = next(
        b["callback_data"]
        for row in rig.transport.messages[-1][1]["inline_keyboard"]
        for b in row
        if b["text"] == rig.t("apply")
    )
    rig.repository.connection.execute(
        "UPDATE jobs SET semantic_key='different' WHERE dedupe_key='linkedin:0'"
    )
    rig.repository.connection.commit()
    callback(rig, data)
    assert rig.repository.application_for("linkedin:0")["state"] == "not_applied"
    rig.repository.connection.execute("DELETE FROM jobs WHERE dedupe_key='linkedin:0'")
    rig.repository.connection.commit()
    rig.detail("linkedin:0")
    assert rig.t("unavailable") == rig.transport.messages[-1][0]


def test_expired_and_invalid_callbacks(rig):
    data = rig.store.token({"action": "home"}, ttl=-1)
    callback(rig, data)
    assert rig.t("expired") in rig.transport.messages[-1][0]
    callback(rig, "cc:malformed")
    assert rig.t("expired") in rig.transport.messages[-1][0]
    assert rig.store.resolve("cc:" + "x" * 100) is None


@pytest.mark.parametrize("sort", ["newest", "priority_sort", "score_sort"])
def test_stable_pagination_snapshot(rig, sort):
    snap = rig.jobs.snapshot("all", sort)
    first, more = rig.jobs.page(snap)
    second, more2 = rig.jobs.page(snap, 1)
    assert more and more2
    rig.repository.upsert(
        [
            Job(
                source="future",
                source_job_id="new",
                title="Fresh",
                company="X",
                location="Vienna",
                snippet="",
                url="https://example.org",
            )
        ]
    )
    rig.jobs.sync(force=True)
    assert first == rig.jobs.page(snap)[0]
    assert second == rig.jobs.page(snap, 1)[0]
    assert not {x["key"] for x in first}.intersection(x["key"] for x in second)
    assert len(rig.jobs.page(snap, 2)[0]) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "new-source"),
        ("work_mode", "hybrid"),
        ("hours", "confirmed_target"),
        ("state", "not_applied"),
        ("priority", "HIGH"),
    ],
)
def test_filters(rig, field, value):
    snap = rig.jobs.snapshot("all", filters={field: value})
    rows, _ = rig.jobs.page(snap)
    assert rows
    callback(rig, payload(rig, action="filter_values", field=field, view="all", filters={}))
    assert rig.transport.messages[-1][1]["inline_keyboard"]


def test_parameters_are_validated(rig):
    with pytest.raises(ValueError):
        rig.jobs.snapshot("all", filters={"source;DROP TABLE jobs": "x"})
    with pytest.raises(ValueError):
        rig.jobs.snapshot("arbitrary")
    with pytest.raises(ValueError):
        rig.jobs.page("unknown", -1)
    assert rig.jobs.options("source") == ["linkedin", "new-source"]


def test_missing_fields_and_message_limits(rig):
    rig.repository.connection.execute(
        "UPDATE jobs SET snippet='',location='',description=? WHERE dedupe_key='linkedin:0'",
        ("😀<b>employer</b>" * 2000,),
    )
    rig.repository.connection.commit()
    rig.detail("linkedin:0")
    text, markup = rig.transport.messages[-1]
    assert len(text.encode("utf-16-le")) <= 4096 * 2
    assert label("uk", "unknown") in text
    assert "<b>employer</b>" in text  # no Telegram parse_mode, so no HTML injection
    assert all(
        len(b.get("callback_data", "").encode()) <= 64
        for row in markup["inline_keyboard"]
        for b in row
    )
    assert "https://" not in str(
        [b.get("callback_data") for row in markup["inline_keyboard"] for b in row]
    )


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://user:pass@example.org",
        "http://[invalid",
    ],
)
def test_unsafe_urls(url):
    assert public_url(url) is None


def test_utf16_truncation():
    assert len(clip("😀" * 5000).encode("utf-16-le")) <= 3900 * 2


def test_ai_and_drafts_never_invoke_provider(rig):
    rig.detail("linkedin:0")
    click(rig, "ai_review")
    assert rig.t("ai_gap") in rig.transport.messages[-1][0]
    assert not rig.repository.connection.execute("SELECT * FROM job_scores").fetchall()
    rig.detail("linkedin:0")
    click(rig, "draft")
    assert rig.t("no_draft") in rig.transport.messages[-1][0]


def test_settings_are_persistent_and_idempotent(rig):
    data = payload(rig, action="notifications", value=False)
    callback(rig, data)
    callback(rig, data)
    assert rig.store.get("notifications") is False
    assert preferences(rig.repository)["notifications"] is False
    callback(rig, payload(rig, action="default_view", value="saved"))
    reopened = ControlStore(rig.search.config.database)
    try:
        assert reopened.get("default_view") == "saved"
    finally:
        reopened.close()


def test_expired_message_replacement_and_ambiguous_send(rig, monkeypatch):
    rig.message_id = 4
    monkeypatch.setattr(
        rig.transport, "edit_message", Mock(side_effect=TelegramRejectedRequest("unavailable"))
    )
    rig.show("replacement")
    assert rig.transport.messages[-1][0] == "replacement"
    monkeypatch.setattr(rig.transport, "edit_message", Mock(side_effect=RuntimeError("timeout")))
    count = len(rig.transport.messages)
    with pytest.raises(RuntimeError):
        rig.show("ambiguous")
    assert len(rig.transport.messages) == count


def test_search_launch_is_real_cli_and_duplicate_protected(rig, monkeypatch):
    calls, handles = [], []

    class Child:
        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))
            handles.append(os.dup(kwargs["pass_fds"][0]))

        def poll(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", Child)
    try:
        state = rig.search.start()
        assert state["status"] == "queued"
        again = rig.search.start()
        assert again["status"] == "running" and len(calls) == 1
        command = json.loads(calls[0][0][-1])
        assert command[:4] == [sys.executable, "-m", "job_radar.cli", "daily"]
        assert "--no-email" in command and "--telegram" not in command
        assert command[command.index("--score-limit") + 1] == "0"
        assert calls[0][1].get("shell") is None
        assert rig.search.status()["status"] == "queued"
    finally:
        for fd in handles:
            os.close(fd)
    assert rig.search.status()["status"] == "interrupted"


def test_shared_daily_lock_prevents_start(rig):
    with rig.search._lock_path(".run.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert rig.search.start()["status"] == "running"


def test_cooldown_failure_and_invalid_profile(rig, monkeypatch):
    rig.search.config = replace(rig.search.config, cooldown=300)
    rig.store.set("search", {"started": time.time(), "status": "success"})
    assert rig.search.start()["status"] == "cooldown"
    rig.store.set("search", {})
    assert rig.search.start("$(bad)")["status"] == "config_missing"
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=OSError("private /path secret")))
    assert rig.search.start()["status"] == "failed"
    assert rig.search.locked(".telegram-search.lock") is False


@pytest.mark.parametrize(
    "changes",
    [{"timeout": 0}, {"limit": 1000}, {"max_pages": 99}, {"transport": "shell"}, {"cooldown": -1}],
)
def test_search_limits(rig, changes):
    with pytest.raises(ValueError):
        replace(rig.search.config, **changes)


def test_search_button_ack_and_replay(rig, monkeypatch):
    monkeypatch.setattr(rig.search, "start", Mock(return_value={"status": "running"}))
    data = payload(rig, action="start_search")
    callback(rig, data)
    callback(rig, data)
    rig.search.start.assert_called_once()
    assert len(rig.transport.answers) == 2


@pytest.mark.parametrize(
    "status", ["running", "success", "partial", "failed", "timeout", "interrupted"]
)
def test_run_status_screens(rig, status):
    state = {
        "status": status,
        "summary": {
            "inserted": 3,
            "shortlisted": 1,
            "sources": [{"status": "success"}, {"status": "failed"}],
        },
        "started": time.time() - 5,
    }
    rig.run_status(state)
    assert rig.t(status) in rig.transport.messages[-1][0]
    assert "1/2" in rig.transport.messages[-1][0]
    assert "3" in rig.transport.messages[-1][0]


@pytest.mark.parametrize("outcome", ["success", "partial", "failed", "timeout"])
def test_worker_supervises_actual_offline_subprocess(rig, tmp_path, outcome):
    config = rig.search.config
    output = tmp_path / "worker-output"
    output.mkdir()
    state = {
        "id": "test-run",
        "status": "queued",
        "started": time.time(),
        "baseline": 0,
        "before_jobs": 12,
        "output": str(output),
    }
    rig.store.set("search", state)
    if outcome in {"success", "partial"}:
        run_id = rig.repository.start_run(0, False, True)
        rig.repository.finish_run(
            run_id,
            status=outcome,
            counts={"inserted": 2, "shortlisted": 1, "sources": []},
            estimated_cost_usd=0,
        )
        (output / "report.json").write_text(json.dumps({"run_id": run_id}))
        command_ = [sys.executable, "-c", "pass"]
    elif outcome == "failed":
        command_ = [sys.executable, "-c", "raise SystemExit(2)"]
    else:
        command_ = [sys.executable, "-c", "import time; time.sleep(10)"]
    fd = os.open(rig.search._lock_path(".telegram-search.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    supervise(config.database, fd, 0.05 if outcome == "timeout" else 3, "test-run", command_)
    final = rig.store.get("search")
    assert final["status"] == outcome
    assert final["ended"] >= state["started"]
    assert rig.search.locked(".telegram-search.lock") is False


def test_no_shared_schema_changes(rig):
    tables = {
        r[0]
        for r in rig.repository.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "callbacks" not in tables and "job_index" not in tables
    assert "application_events" in tables


@pytest.mark.parametrize("language", ["uk", "de", "en"])
def test_localized_real_daily_outbox_replay_without_network(rig, tmp_path, language):
    from job_radar.daily import daily_cycle

    rig.store.set("language", language)
    config = rig.search.config
    report = daily_cycle(
        config.database,
        [],
        lambda _: pytest.fail("no collection"),
        telegram=rig.transport,
        chat_id="42",
        output=tmp_path / "digest",
        day="2026-11-10",
        enrichment_limit=0,
    )
    assert report["telegram_sent_this_run"]
    assert tr(language, "relevant") in rig.transport.messages[-1][0]
    assert report["alert_items"] == report["preview_items"]
    assert report["alert_items"] > 0
    count = len(rig.transport.messages)
    again = daily_cycle(
        config.database,
        [],
        lambda _: None,
        telegram=rig.transport,
        chat_id="42",
        output=tmp_path / "digest2",
        day="2026-11-10",
        enrichment_limit=0,
    )
    assert not again["telegram_sent_this_run"]
    assert len(rig.transport.messages) == count
    button = rig.transport.messages[-1][1]["inline_keyboard"][0][0]
    callback(rig, button["callback_data"])
    assert "Junior Python" in rig.transport.messages[-1][0]


def test_notification_disabled_preserves_backlog(rig, tmp_path):
    from job_radar.daily import daily_cycle

    rig.store.set("notifications", False)
    report = daily_cycle(
        rig.search.config.database,
        [],
        lambda _: None,
        telegram=rig.transport,
        chat_id="42",
        output=tmp_path / "silent",
        enrichment_limit=0,
    )
    assert not rig.transport.messages
    assert report["telegram"] == "disabled"
    assert not rig.repository.connection.execute("SELECT * FROM daily_alert_items").fetchall()


@pytest.mark.parametrize(
    "score_status,recommendation,expected",
    [("success", "apply", True), ("success", "skip", False), ("failed", "apply", False)],
)
def test_existing_scoring_cache_and_failure_states(rig, score_status, recommendation, expected):
    from job_radar.candidate import candidate_profile_hash
    from job_radar.matching import SCORE_PROMPT_VERSION, _effective_vacancy

    profile = {"core_skills": {"languages": ["Python"]}}
    rig.jobs.profile = profile
    rig.jobs.model = "offline"
    row = rig.jobs.detail("linkedin:0")
    rig.repository.save_job_score(
        {
            "score_cache_key": "offline-score",
            "semantic_key": row["semantic_key"],
            "canonical_dedupe_key": "linkedin:0",
            "candidate_profile_hash": candidate_profile_hash(profile),
            "description_hash": _effective_vacancy(row)["description_hash"],
            "prompt_version": SCORE_PROMPT_VERSION,
            "model": "offline",
            "provisional": 1,
            "result_json": json.dumps({"match_score": 80, "recommendation": recommendation}),
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.001,
            "response_id": "",
            "score_status": score_status,
            "score_error": "",
            "created_at": "2026-11-10T12:00:00+00:00",
        }
    )
    actual = rig.jobs.detail("linkedin:0")
    assert actual["recommended"] is expected
    assert actual["score_status"] == score_status
    rig.jobs.profile = None
    assert rig.jobs.detail("linkedin:0")["recommended"] is False


def test_search_results_are_bounded_to_new_native_records(rig):
    rig.repository.upsert(
        [
            Job(
                source="future",
                source_job_id="1",
                title="Fresh",
                company="X",
                location="Vienna",
                snippet="",
                url="https://example.org",
            )
        ]
    )
    snap = rig.jobs.snapshot("all", since={"after": 12, "through": 13})
    rows, more = rig.jobs.page(snap)
    assert rows == [{"key": "future:1"}] and not more


def test_sqlite_busy_reply_is_safe_and_retryable(rig, monkeypatch):
    import sqlite3

    monkeypatch.setattr(
        rig.jobs, "statistics", Mock(side_effect=sqlite3.OperationalError("locked /private/token"))
    )
    command(rig)
    text = rig.transport.messages[-1][0]
    assert text == rig.t("error")
    assert "private" not in text and "token" not in text


@pytest.mark.parametrize("scenario", ["running", "success", "partial", "failed"])
def test_offline_walkthrough_all_outcomes(monkeypatch, capsys, scenario):
    from job_radar.control.demo import main

    monkeypatch.setattr(sys, "argv", ["offline-demo", "--scenario", scenario, "--walkthrough"])
    main()
    assert "OFFLINE WALKTHROUGH PASSED" in capsys.readouterr().out


def test_detached_gateway_to_existing_cli_offline(rig, monkeypatch):
    # Exercise the production launch/supervisor/CLI path. Only collection transport
    # is replaced in the child; daily, persistence, reports, locks, exports are real.
    from pathlib import Path

    real_popen = subprocess.Popen
    monkeypatch.setenv("PYTHONPATH", str(Path("src").resolve()))
    monkeypatch.setenv(
        "JOB_RADAR_CANDIDATE_PROFILE", str(Path("candidate_profile.example.yaml").resolve())
    )

    def start(command, **kwargs):
        daily = json.loads(command[-1])
        program = (
            "import sys; from unittest.mock import patch; "
            "from job_radar.sources.base import CollectionBatch; "
            "from job_radar.cli import main; "
            "sys.argv=" + repr(["job-radar", *daily[3:]]) + "; "
            "replacement=lambda client,url,**kw:CollectionBatch('empty',url,pages_fetched=1); "
            "patch('job_radar.collection.collect_search',replacement).start(); main()"
        )
        command[-1] = json.dumps([sys.executable, "-c", program])
        return real_popen(command, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", start)
    rig.search.config = replace(rig.search.config, transport="http")
    result = rig.search.start()
    assert result["status"] == "queued"
    # Bounded wait on this particular local child, not a scheduler or collector loop.
    rig.search.children[0].wait(timeout=15)
    result = rig.search.status()
    assert result["status"] == "success"
    assert result["summary"]["sources"][0]["status"] == "empty"
    assert result["summary"]["inserted"] == 0
    assert result["run_id"] > 0
    assert rig.search.locked(".run.lock") is False


def test_index_refreshes_same_second_content_changes(rig):
    assert rig.jobs.statistics()["priority"] == [("HIGH", 12)]
    stamp = rig.repository.connection.execute(
        "SELECT updated_at FROM jobs WHERE dedupe_key='linkedin:0'"
    ).fetchone()[0]
    rig.repository.connection.execute(
        "UPDATE jobs SET screening_status='rejected',updated_at=? WHERE dedupe_key='linkedin:0'",
        (stamp,),
    )
    rig.repository.connection.commit()
    rig.jobs.last_sync = 0
    stats = rig.jobs.statistics()
    assert dict(stats["priority"])["EXCLUDED"] == 1


def test_not_modified_edit_does_not_send_duplicate(rig, monkeypatch):
    rig.message_id = 3
    error = TelegramRejectedRequest("unchanged")
    error.not_modified = True
    monkeypatch.setattr(rig.transport, "edit_message", Mock(side_effect=error))
    rig.show("unchanged")
    assert not rig.transport.messages


@pytest.mark.parametrize(
    "owner,chat,environment", [(43, "42", "42"), (None, "0", "0"), (None, "42", "bad")]
)
def test_runtime_fails_closed_before_opening_database(rig, monkeypatch, owner, chat, environment):
    import asyncio
    from types import SimpleNamespace
    from job_radar.control.runtime import poll
    from job_radar.config import Settings

    monkeypatch.setenv("TELEGRAM_OWNER_ID", environment)
    args = SimpleNamespace(owner_id=owner)
    settings = Settings(
        rig.search.config.database,
        rig.search.config.database,
        rig.search.config.database,
        telegram_bot_token="offline-no-network",
        telegram_chat_id=chat,
    )
    with pytest.raises(ValueError):
        asyncio.run(poll(settings, args))


def test_owner_loaded_after_dotenv_parsing(rig, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from job_radar.control import runtime
    from job_radar.config import Settings

    monkeypatch.setenv("TELEGRAM_OWNER_ID", "42")

    class StopBeforeNetwork(Exception):
        pass

    monkeypatch.setattr(runtime, "RunLock", Mock(side_effect=StopBeforeNetwork))
    args = SimpleNamespace(owner_id=None, database=None)
    settings = Settings(
        rig.search.config.database,
        rig.search.config.database,
        rig.search.config.database,
        telegram_bot_token="offline-no-network",
        telegram_chat_id="42",
    )
    with pytest.raises(StopBeforeNetwork):
        asyncio.run(runtime.poll(settings, args))


@pytest.mark.parametrize("selection,count", [("p:karriere", 1), ("s:karriere", 2), ("all", 2)])
def test_search_profile_source_namespaces(rig, monkeypatch, selection, count):
    from job_radar.searches import SavedSearch

    monkeypatch.setattr(
        rig.search,
        "profiles",
        lambda: [
            SavedSearch("karriere", "karriere", "https://www.karriere.at/jobs/python/wien"),
            SavedSearch("other", "karriere", "https://www.karriere.at/jobs/java/wien"),
        ],
    )
    monkeypatch.setattr(subprocess, "Popen", Mock())
    result = rig.search.start(selection)
    from pathlib import Path

    config = yaml.safe_load((Path(result["output"]) / "searches.yaml").read_text())
    assert len(config["searches"]) == count


@pytest.mark.parametrize("changed", [False, True])
def test_verified_legacy_digest_identity(rig, changed):
    import hashlib

    row = rig.jobs.detail("linkedin:0")
    with rig.repository.connection:
        rig.repository.connection.execute(
            "INSERT INTO notification_outbox(notification_key,payload,updated_at) VALUES ('legacy','{}','now')"
        )
        rig.repository.connection.execute(
            "INSERT INTO daily_alert_items VALUES (?,?,?,'legacy')",
            (hashlib.sha256(b"42").hexdigest()[:16], "linkedin:0", row["semantic_key"]),
        )
        if changed:
            rig.repository.connection.execute(
                "UPDATE jobs SET semantic_key='replaced' WHERE dedupe_key='linkedin:0'"
            )
    callback(rig, "applyjob:linkedin:0")
    assert rig.repository.application_for("linkedin:0")["state"] == (
        "not_applied" if changed else "applied"
    )
