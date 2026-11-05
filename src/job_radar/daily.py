"""Bounded multi-source cycle with durable state and one daily digest."""

import hashlib
import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from job_radar.availability import is_expired
from job_radar.errors import error_label
from job_radar.exporting import semantic_groups
from job_radar.ingestion import ingest_observations
from job_radar.parsers import parse_email
from job_radar.runner import RunLock
from job_radar.screening import load_rules
from job_radar.storage import JobRepository
from job_radar.work_hours import assess_hours


def review_rows(repository, profile_hash=None, model=None):
    from job_radar.matching import application_queue

    scoring = {
        row["semantic_key"]: row
        for row in application_queue(
            repository, all_active=True, profile_hash=profile_hash, model=model
        )
    }
    rows = []
    for group in semantic_groups(repository.all()):
        row = dict(group[0])
        state = repository.application_for(row["dedupe_key"])
        row.update(application_state=state["state"], application_revision=state["revision"])
        row["application_key"] = state.get("dedupe_key", row["dedupe_key"])
        current = scoring.get(row["semantic_key"], {})
        for key in (
            "match_score",
            "recommendation",
            "score_confidence",
            "provisional",
            "score_status",
        ):
            row[key] = current.get(key)
        row.update(assess_hours(row))
        row["alternate_source_urls"] = [x["url"] for x in group[1:]]
        row["source_keys"] = [x["dedupe_key"] for x in group]
        rows.append(row)
    return rows


def _reviewable(row):
    return (
        not is_expired(row)
        and row["screening_status"] in {"eligible", "review"}
        and row["application_state"] in {"not_applied", "saved"}
        and row["hours_status"] != "full_time_only"
    )


def _already_alerted(repository, row, chat_hash):
    # Both native identities and the current semantic identity survive canonical switches.
    for key in row["source_keys"]:
        if repository.connection.execute(
            """SELECT 1 FROM daily_alert_items a JOIN notification_outbox n
            ON n.notification_key=a.notification_key
            WHERE a.chat_hash=? AND (a.dedupe_key=? OR a.semantic_key=?)
            AND n.status IN ('sent', 'unknown') LIMIT 1""",
            (chat_hash, key, row["semantic_key"]),
        ).fetchone():
            return True
    legacy = repository.telegram_delivery(row["semantic_key"])
    if legacy and legacy["delivery_status"] in {"delivered", "applied", "skipped"}:
        return True
    # Upgrade compatibility: older daily digests stored text but no item identities.
    urls = {row["url"], *row["alternate_source_urls"]}
    for old in repository.connection.execute(
        "SELECT payload FROM notification_outbox WHERE notification_key LIKE ? "
        "AND status IN ('sent', 'unknown')",
        (f"daily:%:{chat_hash}",),
    ):
        if urls.intersection(json.loads(old[0]).get("text", "").splitlines()):
            return True
    return False


def _write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def _deliver(repository, report, rows, telegram, chat_id, day, output):
    from job_radar.telegram import TelegramRejectedRequest

    chat_hash = hashlib.sha256(chat_id.encode()).hexdigest()[:16]
    report["unknown_deliveries"] = repository.connection.execute(
        "SELECT COUNT(*) FROM notification_outbox WHERE notification_key LIKE ? AND status='unknown'",
        (f"daily:%:{chat_hash}",),
    ).fetchone()[0]
    if report["unknown_deliveries"]:
        report["errors"].append("Unresolved Telegram delivery; inspect outbox before recovery")
    candidates = [row for row in rows if not _already_alerted(repository, row, chat_hash)]
    report["newly_relevant"] = len(candidates)
    text = (
        f"Job Radar · {day}\nNew source listings: {report['inserted']} · "
        f"shortlisted: {report['shortlisted']} · not alerted: {len(candidates)}\n"
        f"Scored: {report['scoring']['scored']} · failed: {report['scoring']['failed']}\n"
        f"Estimated API cost: ${report['estimated_cost_usd']:.6f}\n"
    )
    text += "\n".join(f"{x['name']}: {x['status']} ({x['jobs']})" for x in report["sources"])
    if report["errors"]:
        text += "\nPartial results: " + "; ".join(report["errors"])
    else:
        text += "\nCollection completed within configured limits."
    if not report["scoring"]["requested"]:
        text += "\nRule-based shortlist; AI scoring disabled."
    # Bound the preamble too; errors never contain raw external response bodies.
    text = text[:1800]
    buttons, selected = [], []
    for row in candidates[:8]:
        score = row.get("match_score")
        label = (
            f"Technical match: {score}/100" if score is not None else "Technical match: unscored"
        )
        if row.get("provisional") and score is not None:
            label += " (provisional)"
        line = (
            f"\n\n{row['title'][:120]} · {row['company'][:80]}\n{label}\n"
            f"Hours: {row['hours_status']}\n{row['url']}"
        )
        if len((text + line).encode("utf-16-le")) // 2 > 3800:
            break
        text += line
        selected.append(row)
        buttons.append(
            [
                {
                    "text": f"Applied: {row['title'][:25]}",
                    "callback_data": f"applyjob:{row['application_key']}",
                },
                {"text": "Skip", "callback_data": f"skipjob:{row['application_key']}"},
            ]
        )
    (output / "telegram-preview.txt").write_text(text)
    report["preview_items"] = len(selected)
    report["alert_items"] = 0
    if telegram is None:
        return
    key = f"daily:{day}:{chat_hash}"
    # Reserve item identities and the immutable payload atomically before any remote call.
    with repository.connection:
        inserted = repository.connection.execute(
            "INSERT OR IGNORE INTO notification_outbox(notification_key,payload,updated_at) "
            "VALUES (?, ?, ?)",
            (
                key,
                json.dumps(
                    {
                        "text": text,
                        "reply_markup": {"inline_keyboard": buttons} if buttons else None,
                    },
                    ensure_ascii=False,
                ),
                datetime.now(timezone.utc).isoformat(),
            ),
        ).rowcount
        if inserted:
            for row in selected:
                for source_key in row["source_keys"]:
                    repository.connection.execute(
                        "INSERT OR REPLACE INTO daily_alert_items VALUES (?, ?, ?, ?)",
                        (chat_hash, source_key, row["semantic_key"], key),
                    )
    pending = repository.connection.execute(
        "SELECT * FROM notification_outbox WHERE notification_key=?",
        (key,),
    ).fetchone()
    if pending["status"] != "pending":
        report["telegram"] = pending["status"]
        report["telegram_sent_this_run"] = False
        if pending["status"] == "unknown":
            report["errors"].append("Prior Telegram send is unknown; inspect before recovery")
        return
    repository.connection.execute(
        "UPDATE notification_outbox SET status='unknown', updated_at=? WHERE notification_key=?",
        (datetime.now(timezone.utc).isoformat(), key),
    )
    repository.connection.commit()
    try:
        payload = json.loads(pending["payload"])
        message_id = telegram.send_message(chat_id, payload["text"], payload["reply_markup"])
        repository.connection.execute(
            "UPDATE notification_outbox SET status='sent', message_id=?, updated_at=? "
            "WHERE notification_key=?",
            (message_id, datetime.now(timezone.utc).isoformat(), key),
        )
        repository.connection.commit()
        report["telegram"] = "sent"
        report["telegram_sent_this_run"] = True
        report["alert_items"] = len((payload.get("reply_markup") or {}).get("inline_keyboard", []))
    except TelegramRejectedRequest:
        repository.connection.execute(
            "UPDATE notification_outbox SET status='pending' WHERE notification_key=?",
            (key,),
        )
        repository.connection.commit()
        report["errors"].append(
            "Telegram rejected delivery; fix configuration/rate limit and rerun"
        )
        report["telegram"] = "failed"
    except Exception:
        report["errors"].append("Telegram delivery unknown; inspect before recovery")
        report["telegram"] = "unknown"


def daily_cycle(
    database: Path,
    searches,
    collect,
    *,
    fetch_emails=None,
    provider=None,
    profile=None,
    score_limit=0,
    telegram=None,
    chat_id="",
    output=Path("outputs/daily"),
    import_workbook=None,
    input_cost_per_million=0,
    output_cost_per_million=0,
    on_progress=None,
    initial_errors=(),
    score_retry_failed=False,
    day=None,
    email_budget_seconds=120,
):
    def progress(message):
        if on_progress is not None:
            on_progress(message)

    day = day or datetime.now(ZoneInfo("Europe/Vienna")).date().isoformat()
    report = {
        "sources": [],
        "emails": 0,
        "inserted": 0,
        "changed": 0,
        "unchanged": 0,
        "errors": list(initial_errors),
        "estimated_cost_usd": 0,
        "unique_jobs": 0,
        "eligible_for_scoring": 0,
        "shortlisted": 0,
        "review_count": 0,
        "newly_relevant": 0,
        "telegram": "disabled",
        "telegram_sent_this_run": False,
        "scoring": {
            "requested": score_limit > 0,
            "scored": 0,
            "cached": 0,
            "failed": 0,
            "skipped": 0,
            "status": "disabled" if not score_limit else "pending",
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    with (
        RunLock(database.with_suffix(database.suffix + ".run.lock")),
        JobRepository(database) as repository,
    ):
        # The shared lock proves no other pipeline run is active on this database.
        repository.connection.execute(
            "UPDATE run_history SET status='interrupted', ended_at=?, "
            "error='Previous process stopped before completion' WHERE status='running'",
            (datetime.now(timezone.utc).isoformat(),),
        )
        repository.connection.commit()
        run_id = repository.start_run(10, False, telegram is None)
        report["run_id"] = run_id
        rows = []
        try:
            if import_workbook:
                try:
                    import shutil
                    from job_radar.workbook import import_status_edits

                    archive = output / "imports"
                    archive.mkdir(exist_ok=True)
                    digest = hashlib.sha256(import_workbook.read_bytes()).hexdigest()[:16]
                    archived = archive / f"input-{digest}.xlsx"
                    if import_workbook.resolve() != archived.resolve():
                        shutil.copy2(import_workbook, archived)
                    imported = import_status_edits(
                        repository, archived, output / "import-receipt.json"
                    )
                    if any(row["result"] == "rejected" for row in imported):
                        report["errors"].append("Some Excel edits rejected; see import receipt")
                except Exception as error:
                    report["errors"].append(f"Excel import: {error_label(error)}")
            if fetch_emails:
                progress("Reading email alerts...")
                email_errors = []
                email_started = time.monotonic()
                try:
                    for message in fetch_emails():
                        try:
                            jobs = [
                                replace(
                                    job,
                                    connector="gmail",
                                    observed_at=datetime.now(timezone.utc).isoformat(),
                                )
                                for job in parse_email(message)
                            ]
                            result = repository.upsert_detailed(jobs)
                            for job in jobs:
                                repository.record_observation(job)
                            for key in ("inserted", "changed", "unchanged"):
                                report[key] += getattr(result, key)
                            report["emails"] += 1
                        except Exception as error:
                            email_errors.append(f"Email parse: {error_label(error)}")
                        if time.monotonic() - email_started >= email_budget_seconds:
                            email_errors.append(
                                "Email time budget reached; remaining alerts deferred"
                            )
                            break
                except Exception as error:
                    reason = (
                        "reauthorize Gmail (invalid_grant)"
                        if "invalid_grant" in str(error)
                        else error_label(error)
                    )
                    email_errors.append(f"Email: {reason}")
                report["errors"].extend(email_errors)
                report["sources"].append(
                    {
                        "name": "gmail",
                        "status": "failed" if email_errors else "success",
                        "jobs": report["inserted"] + report["changed"] + report["unchanged"],
                        "emails": report["emails"],
                    }
                )
                repository.record_run_stage(
                    run_id,
                    "email",
                    status="partial" if email_errors else "success",
                    item_count=report["emails"],
                    error="; ".join(email_errors),
                )
            for search in searches:
                progress(f"Collecting {search.name}...")
                try:
                    batch = collect(search.url)
                    result = ingest_observations(repository, batch.jobs)
                    for key in ("inserted", "changed", "unchanged"):
                        report[key] += getattr(result, key)
                    report["sources"].append(
                        {
                            "name": search.name,
                            "status": batch.status,
                            "jobs": len(batch.jobs),
                            "pages": batch.pages_fetched,
                            "stop": batch.stop_reason,
                        }
                    )
                    progress(
                        f"{search.name}: {batch.status}, {len(batch.jobs)} jobs, {batch.pages_fetched} pages"
                    )
                    reason = (
                        ""
                        if batch.status in {"success", "empty"}
                        else f"{search.name}: {batch.status} ({batch.stop_reason})"
                    )
                    if reason:
                        report["errors"].append(reason)
                    repository.record_run_stage(
                        run_id,
                        search.name,
                        status=batch.status,
                        item_count=len(batch.jobs),
                        error=reason,
                    )
                except Exception as error:
                    reason = f"{search.name}: {error_label(error)}"
                    report["errors"].append(reason)
                    report["sources"].append({"name": search.name, "status": "failed", "jobs": 0})
                    repository.record_run_stage(run_id, search.name, status="failed", error=reason)
            progress("Screening vacancies and preparing review files...")
            rules = load_rules()
            keys = [
                row["dedupe_key"]
                for row in repository.all()
                if row["screening_rules_version"] != rules["version"] or row["description"]
            ]
            report["screening"] = repository.screen_keys(keys)
            profile_hash = None
            if profile is not None:
                from job_radar.candidate import candidate_profile_hash

                profile_hash = candidate_profile_hash(profile)
            model = provider.model if provider else None
            rows = review_rows(repository, profile_hash, model)
            score_keys = {
                row["semantic_key"]
                for row in rows
                if _reviewable(row) and row["screening_status"] == "eligible"
            }
            report["eligible_for_scoring"] = len(score_keys)
            if score_limit:
                if provider is None:
                    report["scoring"]["status"] = "unavailable"
                    report["errors"].append(
                        "Scoring unavailable; check API key and candidate profile"
                    )
                else:
                    from job_radar.matching import score_jobs

                    try:
                        progress(f"Scoring at most {score_limit} eligible vacancies...")
                        result = score_jobs(
                            repository,
                            provider,
                            profile or {},
                            only="eligible",
                            limit=score_limit,
                            retry_failed=score_retry_failed,
                            semantic_keys=score_keys,
                            retry_after_seconds=86400,
                            input_cost_per_million=input_cost_per_million,
                            output_cost_per_million=output_cost_per_million,
                        )
                        report["estimated_cost_usd"] = result.estimated_cost_usd
                        report["cost_may_exclude_failed_requests"] = result.failed > 0
                        report["scoring"].update(
                            scored=result.scored,
                            cached=result.cached,
                            failed=result.failed,
                            skipped=result.skipped,
                            status="partial" if result.failed else "success",
                        )
                        report["errors"].extend(f"Scoring: {e}" for e in result.errors)
                    except Exception as error:
                        report["scoring"]["status"] = "failed"
                        report["errors"].append(f"Scoring: {error_label(error)}")
                repository.record_run_stage(
                    run_id,
                    "scoring",
                    status=report["scoring"]["status"],
                    item_count=report["scoring"]["scored"],
                    error="; ".join(e for e in report["errors"] if e.startswith("Scoring")),
                )
            rows = review_rows(repository, profile_hash, model)
            report["eligible_score_states"] = {
                state: sum(
                    row["semantic_key"] in score_keys and row.get("score_status") == state
                    for row in rows
                )
                for state in ("success", "failed", "not_started")
            }
            report["total_source_jobs"] = len(repository.all())
            report["unique_jobs"] = len(rows)
            report["discovered"] = report["inserted"] + report["changed"] + report["unchanged"]
            _write_json(output / "vacancies.json", rows)
            try:
                from job_radar.workbook import export_workbook

                report["workbook"] = str(export_workbook(rows, output / "vacancies.xlsx"))
            except Exception as error:
                report["errors"].append(f"Excel export: {error_label(error)}")
        except Exception as error:
            report["errors"].append(f"Daily cycle: {error_label(error)}")
        eligible = [row for row in rows if _reviewable(row)]
        report["review_count"] = len(eligible)
        # With scoring enabled, failed/unscored/skip roles remain in Excel, not alerts.
        shortlisted = [
            row
            for row in eligible
            if not score_limit
            or (
                row.get("score_status") == "success"
                and (
                    row.get("recommendation") in {"strong_apply", "apply"}
                    or (
                        row.get("recommendation") == "review"
                        and (row.get("match_score") or 0) >= 50
                    )
                )
            )
        ]
        shortlisted.sort(
            key=lambda row: (
                row["hours_status"] == "unknown",
                -(row.get("match_score") or 0),
                row["dedupe_key"],
            )
        )
        report["shortlisted"] = len(shortlisted)
        try:
            _deliver(repository, report, shortlisted, telegram, chat_id, day, output)
        except Exception as error:
            report["errors"].append(f"Digest: {error_label(error)}")
            report["telegram"] = "failed" if telegram else "disabled"
        repository.record_run_stage(
            run_id,
            "telegram",
            status=report["telegram"],
            item_count=int(report["telegram_sent_this_run"]),
        )
        report["status"] = "partial" if report["errors"] else "success"
        repository.finish_run(
            run_id,
            status=report["status"],
            counts=report,
            estimated_cost_usd=report["estimated_cost_usd"],
            error="; ".join(report["errors"]),
        )
        try:
            from job_radar.backups import backup_database

            report["backup"] = str(backup_database(database, output / "backups"))
        except Exception as error:
            report["errors"].append(f"Backup: {error_label(error)}")
            report["status"] = "partial"
        repository.finish_run(
            run_id,
            status=report["status"],
            counts=report,
            estimated_cost_usd=report["estimated_cost_usd"],
            error="; ".join(report["errors"]),
        )
        (output / "runs").mkdir(exist_ok=True)
        _write_json(output / "runs" / f"run-{run_id}.json", report)
        _write_json(output / "report.json", report)
    return report
