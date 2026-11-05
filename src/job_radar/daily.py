"""A bounded daily collection cycle. Paid scoring and delivery are opt-in."""

import json
import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from job_radar.exporting import semantic_groups
from job_radar.ingestion import ingest_observations
from job_radar.parsers import parse_email
from job_radar.runner import RunLock
from job_radar.screening import load_rules
from job_radar.storage import JobRepository
from job_radar.availability import is_expired


def review_rows(repository):
    from job_radar.matching import application_queue

    scoring = {row["semantic_key"]: row for row in application_queue(repository, all_active=True)}
    rows = []
    for group in semantic_groups(repository.all()):
        row = dict(group[0])
        state = repository.application_for(row["dedupe_key"])
        row.update(application_state=state["state"], application_revision=state["revision"])
        row["application_key"] = state.get("dedupe_key", row["dedupe_key"])
        current = scoring.get(row["semantic_key"], {})
        for key in ("match_score", "recommendation", "score_confidence", "provisional"):
            row[key] = current.get(key)
        row["alternate_source_urls"] = [x["url"] for x in group[1:]]
        rows.append(row)
    return rows


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
):
    def progress(message):
        if on_progress is not None:
            on_progress(message)

    report = {
        "sources": [],
        "emails": 0,
        "inserted": 0,
        "changed": 0,
        "unchanged": 0,
        "errors": [],
        "estimated_cost_usd": 0,
    }
    output.mkdir(parents=True, exist_ok=True)
    with (
        RunLock(database.with_suffix(database.suffix + ".run.lock")),
        JobRepository(database) as repository,
    ):
        run_id = repository.start_run(10, False, telegram is None)
        try:
            if import_workbook:
                import shutil
                from job_radar.workbook import import_status_edits

                archive = output / "imports"
                archive.mkdir(exist_ok=True)
                digest = hashlib.sha256(import_workbook.read_bytes()).hexdigest()[:16]
                archived = archive / f"input-{digest}.xlsx"
                if import_workbook.resolve() != archived.resolve():
                    shutil.copy2(import_workbook, archived)
                imported = import_status_edits(repository, archived, output / "import-receipt.json")
                if any(row["result"] == "rejected" for row in imported):
                    report["errors"].append("Some Excel edits were rejected; see import receipt")
            # Email failures cannot cancel direct collection; commit each decoded message.
            if fetch_emails:
                progress("Reading email alerts...")
                try:
                    for message in fetch_emails():
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
                    repository.record_run_stage(
                        run_id, "email", status="success", item_count=report["emails"]
                    )
                except Exception as error:
                    reason = (
                        "reauthorize Gmail (invalid_grant)"
                        if "invalid_grant" in str(error)
                        else type(error).__name__
                    )
                    report["errors"].append(f"Email: {reason}")
                    repository.record_run_stage(
                        run_id, "email", status="failed", error=type(error).__name__
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
                        f"{search.name}: {batch.status}, {len(batch.jobs)} jobs, "
                        f"{batch.pages_fetched} pages"
                    )
                    if batch.status not in {"success", "empty"}:
                        report["errors"].append(f"{search.name}: {batch.status}; {batch.error}")
                    repository.record_run_stage(
                        run_id, search.name, status=batch.status, item_count=len(batch.jobs)
                    )
                except Exception as error:
                    report["errors"].append(f"{search.name}: {type(error).__name__}")
            # Include outdated rules and newly supplied full descriptions, not just new rows.
            progress("Screening vacancies and preparing review files...")
            rules = load_rules()
            keys = [
                row["dedupe_key"]
                for row in repository.all()
                if row["screening_rules_version"] != rules["version"] or row["description"]
            ]
            report["screening"] = repository.screen_keys(keys)
            if score_limit:
                if provider is None:
                    report["errors"].append("Scoring requested but OPENAI_API_KEY is missing")
                else:
                    from job_radar.matching import score_jobs

                    result = score_jobs(
                        repository,
                        provider,
                        profile or {},
                        limit=score_limit,
                        input_cost_per_million=input_cost_per_million,
                        output_cost_per_million=output_cost_per_million,
                    )
                    report["estimated_cost_usd"] = result.estimated_cost_usd
                    report["scoring"] = {
                        "scored": result.scored,
                        "cached": result.cached,
                        "failed": result.failed,
                    }
                    report["errors"].extend(result.errors)
            rows = review_rows(repository)
            (output / "vacancies.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
            try:
                from job_radar.workbook import export_workbook

                report["workbook"] = str(export_workbook(rows, output / "vacancies.xlsx"))
            except Exception as error:
                report["errors"].append(f"Excel export: {type(error).__name__}")
            eligible = [
                row
                for row in rows
                if not is_expired(row)
                and row["screening_status"] in {"eligible", "review"}
                and row["application_state"] in {"not_applied", "saved"}
            ]
            report["review_count"] = len(eligible)
            eligible.sort(
                key=lambda row: (
                    row.get("recommendation") != "strong_apply",
                    row.get("recommendation") != "apply",
                    -(row.get("match_score") or 0),
                )
            )
            day = datetime.now(ZoneInfo("Europe/Vienna")).date().isoformat()
            text = f"Job Radar · {day}\nNew: {report['inserted']} · review: {len(eligible)}\n"
            text += "\n".join(
                f"{x['name']}: {x['status']} ({x['jobs']})" for x in report["sources"]
            )
            text += "\n" + (
                "Partial results: " + "; ".join(report["errors"])
                if report["errors"]
                else "Collection completed within configured limits."
            )
            buttons = []
            for row in eligible[:8]:
                line = f"\n\n{row['title'][:120]} · {row['company'][:80]}\n{row['url']}"
                if len((text + line).encode("utf-16-le")) // 2 > 3800:
                    break
                text += line
                buttons.append(
                    [
                        {
                            "text": f"Подався: {row['title'][:25]}",
                            "callback_data": f"applyjob:{row['application_key']}",
                        },
                        {
                            "text": "Пропустити",
                            "callback_data": f"skipjob:{row['application_key']}",
                        },
                    ]
                )
            (output / "telegram-preview.txt").write_text(text)
            if telegram:
                chat_hash = hashlib.sha256(chat_id.encode()).hexdigest()[:16]
                key = f"daily:{day}:{chat_hash}"
                repository.connection.execute(
                    "INSERT OR IGNORE INTO notification_outbox(notification_key,payload,updated_at) VALUES (?, ?, ?)",
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
                )
                repository.connection.commit()
                pending = repository.connection.execute(
                    "SELECT * FROM notification_outbox WHERE notification_key=?", (key,)
                ).fetchone()
                if pending["status"] == "pending":
                    # Mark before sending: an interrupted send is unknown, not safely retryable.
                    repository.connection.execute(
                        "UPDATE notification_outbox SET status='unknown' WHERE notification_key=?",
                        (key,),
                    )
                    repository.connection.commit()
                    try:
                        payload = json.loads(pending["payload"])
                        message_id = telegram.send_message(
                            chat_id, payload["text"], payload["reply_markup"]
                        )
                        repository.connection.execute(
                            "UPDATE notification_outbox SET status='sent', message_id=? WHERE notification_key=?",
                            (message_id, key),
                        )
                        repository.connection.commit()
                        report["telegram"] = "sent"
                    except Exception:
                        report["errors"].append(
                            "Telegram delivery unknown; inspect before resending"
                        )
                        report["telegram"] = "unknown"
                else:
                    report["telegram"] = pending["status"]
                    if pending["status"] == "unknown":
                        report["errors"].append(
                            "Prior Telegram send is unknown; inspect before resending"
                        )
        except Exception as error:
            report["errors"].append(f"Daily cycle: {type(error).__name__}")
        report["status"] = "partial" if report["errors"] else "success"
        repository.finish_run(
            run_id,
            status=report["status"],
            counts={
                "inserted": report["inserted"],
                "changed": report["changed"],
                "unchanged": report["unchanged"],
            },
            estimated_cost_usd=report["estimated_cost_usd"],
            error="; ".join(report["errors"]),
        )
    try:
        from job_radar.backups import backup_database

        report["backup"] = str(backup_database(database, output / "backups"))
    except Exception as error:
        report["errors"].append(f"Backup: {type(error).__name__}")
        report["status"] = "partial"
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report
