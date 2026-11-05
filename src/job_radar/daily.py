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
from job_radar.eligibility import assess_application, application_sort_key, load_policy


def review_rows(repository, profile_hash=None, model=None, profile=None):
    from job_radar.matching import (
        application_queue,
        _effective_vacancy,
        SCORE_PROMPT_VERSION,
        has_full_description,
    )

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
        if profile_hash is not None and model is not None:
            identity = (
                row["semantic_key"],
                profile_hash,
                _effective_vacancy(row)["description_hash"],
                SCORE_PROMPT_VERSION,
                model,
            )
            score = repository.reusable_job_score(*identity) or repository.failed_job_score(
                *identity
            )
            current = {"score_status": "not_started"}
            if score:
                current = {
                    **json.loads(score["result_json"]),
                    "score_status": score["score_status"],
                    "scored_at": score["created_at"],
                    "score_error": score["score_error"],
                }
        elif model is not None and profile_hash is None:
            # Unknown candidate context cannot establish a current successful/failed score.
            current = {"score_status": "context_unavailable" if current else "not_started"}
        for key in (
            "match_score",
            "recommendation",
            "score_confidence",
            "provisional",
            "score_status",
            "scored_at",
        ):
            row[key] = current.get(key)
        row.update(assess_application(row, profile))
        row["jd_status"] = "full" if has_full_description(row) else "incomplete"
        row["first_seen_at"] = min(x["created_at"] for x in group)
        row["review_tier"] = (
            "A"
            if row["score_status"] == "success"
            else "B"
            if row["score_status"] in {None, "not_started"} and _reviewable(row)
            else None
        )
        row["alternate_source_urls"] = [x["url"] for x in group[1:]]
        row["source_keys"] = [x["dedupe_key"] for x in group]
        rows.append(row)
    return sorted(rows, key=application_sort_key)


def review_tiers(rows):
    """Successful recommendations and honestly unscored review are independent queues."""
    tier_a, tier_b = [], []
    for row in rows:
        if not _reviewable(row):
            continue
        if row["review_tier"] == "A" and (
            row.get("recommendation") in {"strong_apply", "apply"}
            or (row.get("recommendation") == "review" and (row.get("match_score") or 0) >= 50)
        ):
            tier_a.append(row)
        elif row["review_tier"] == "B":
            tier_b.append(row)

    return sorted(tier_a, key=application_sort_key), sorted(tier_b, key=application_sort_key)


def _reviewable(row):
    return (
        not is_expired(row)
        and row["screening_status"] in {"eligible", "review"}
        and row["application_state"] in {"not_applied", "saved"}
        and row.get("application_eligibility") != "ineligible"
    )


def _scoreable(row):
    return _reviewable(row) and row.get("role_fit") in {
        "strong",
        "secondary",
        "relevant",
        "adjacent",
    }


def _already_alerted(repository, row, chat_hash, include_unknown=True):
    # Both native identities and the current semantic identity survive canonical switches.
    for key in row["source_keys"]:
        if repository.connection.execute(
            """SELECT 1 FROM daily_alert_items a JOIN notification_outbox n
            ON n.notification_key=a.notification_key
            WHERE a.chat_hash=? AND (a.dedupe_key=? OR a.semantic_key=?)
            AND (n.status='sent' OR (? AND n.status='unknown')) LIMIT 1""",
            (chat_hash, key, row["semantic_key"], include_unknown),
        ).fetchone():
            return True
    legacy = repository.telegram_delivery(row["semantic_key"])
    if legacy and legacy["delivery_status"] in {"delivered", "applied", "skipped"}:
        return True
    # Upgrade compatibility: older daily digests stored text but no item identities.
    urls = {row["url"], *row["alternate_source_urls"]}
    for old in repository.connection.execute(
        "SELECT payload FROM notification_outbox WHERE notification_key LIKE ? "
        "AND (status='sent' OR (? AND status='unknown'))",
        (f"daily:%:{chat_hash}", include_unknown),
    ):
        if urls.intersection(json.loads(old[0]).get("text", "").splitlines()):
            return True
    return False


def _write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def _enrich_review(repository, rows, client, limit, profile, model, new_keys, turn):
    from job_radar.enrichment import enrich_jobs
    from job_radar.matching import has_full_description, scoring_plan

    now = datetime.now(timezone.utc)
    keys = set()
    for row in rows:
        if not _scoreable(row) or has_full_description(row):
            continue
        if row["fetch_status"] in {"snippet_only", "failed", "blocked", "empty"}:
            try:
                stamp = datetime.fromisoformat(row["fetched_at"].replace("Z", "+00:00"))
                if (
                    now - stamp.replace(tzinfo=stamp.tzinfo or timezone.utc)
                ).total_seconds() < 86400:
                    continue
            except ValueError:
                pass
        keys.add(row["semantic_key"])
    selected, _ = scoring_plan(
        repository,
        profile or {},
        model or "",
        limit=limit,
        semantic_keys=keys,
        new_semantic_keys=new_keys,
        fairness_turn=turn,
        only=None,
        retry_after_seconds=86400,
    )
    ordered = [row["semantic_key"] for row in selected]
    result = enrich_jobs(
        repository,
        client,
        only=None,
        semantic_keys=set(ordered),
        retry_failed=True,
        request_limit=limit,
        max_seconds=120,
        ordered_keys=ordered,
    )
    # Newly loaded requirements may hard-reject a role before any paid scoring.
    repository.screen_keys(
        row["dedupe_key"] for row in repository.all() if row["semantic_key"] in ordered
    )
    return result


def _funnel(repository, rows, tier_a, tier_b, chat_id, report):
    from job_radar.matching import has_full_description

    active = [row for row in rows if not is_expired(row)]
    eligible = [row for row in active if _scoreable(row)]
    failed = [row for row in eligible if row["score_status"] == "failed"]
    now = datetime.now(timezone.utc)
    cooling = 0
    for row in failed:
        if row.get("scored_at"):
            stamp = datetime.fromisoformat(row["scored_at"].replace("Z", "+00:00"))
            cooling += (
                now - stamp.replace(tzinfo=stamp.tzinfo or timezone.utc)
            ).total_seconds() < 86400
    chat_hash = hashlib.sha256(chat_id.encode()).hexdigest()[:16]
    pending = sum(not _already_alerted(repository, row, chat_hash) for row in tier_a + tier_b)
    return {
        "database_totals": {
            "unique_active": len(active),
            "eligible_for_ai": len(eligible),
            "current_scored": sum(row["score_status"] == "success" for row in active),
            "eligible_current_scored": sum(row["score_status"] == "success" for row in eligible),
            "unscored": sum(row["score_status"] in {None, "not_started"} for row in eligible),
            "failed": len(failed),
            "retry_deferred": cooling,
            "ai_recommended": len(tier_a),
            "rule_based_review": len(tier_b),
            "confirmed_delivered": sum(
                _already_alerted(repository, row, chat_hash, False) for row in rows
            ),
            "pending_notification_backlog": pending,
            "full_jd_active": sum(has_full_description(row) for row in active),
            "incomplete_jd_active": sum(not has_full_description(row) for row in active),
            "application_priorities": {
                level: sum(row["application_priority"] == level for row in rows)
                for level in ("HIGH", "MEDIUM", "LOW", "EXCLUDED")
            },
            "hours_fits": {
                fit: sum(row["hours_fit"] == fit for row in rows)
                for fit in (
                    "confirmed_target",
                    "acceptable",
                    "flexible_part_time",
                    "unknown",
                    "confirmed_mismatch",
                )
            },
            "confirmed_part_time_matches": sum(
                row["hours_fit"] in {"confirmed_target", "acceptable", "flexible_part_time"}
                and row["application_eligibility"] != "ineligible"
                for row in rows
            ),
            "seniority_mismatches": sum(row["seniority_fit"] == "mismatch" for row in rows),
            "full_time_mismatches": sum(row['hours_mismatch_kind']=='full_time_only' for row in rows),
            "hours_mismatches": sum(row['hours_fit']=='confirmed_mismatch' for row in rows),
            "stored_scoring_cost_usd": round(
                repository.connection.execute(
                    "SELECT COALESCE(SUM(estimated_cost_usd),0) FROM job_scores"
                ).fetchone()[0],
                8,
            ),
        },
        "this_run": {
            "observations": report.get("discovered", 0),
            "inserted": report["inserted"],
            "changed": report["changed"],
            "scored": report["scoring"]["scored"],
            "score_failures": report["scoring"]["failed"],
            "delivered_items": report.get("alert_items", 0),
            "digest_sent": report["telegram_sent_this_run"],
            "estimated_api_cost_usd": report["estimated_cost_usd"],
        },
    }


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
    text += "\nTier A: AI recommended · Tier B: UNSCORED, human review."
    text += (
        f"\nTier A: {report['review_tiers']['ai_recommended']} · "
        f"Tier B: {report['review_tiers']['awaiting_ai']}"
    )
    # Bound the preamble too; errors never contain raw external response bodies.
    text = text[:1800]
    buttons, selected = [], []
    ordered = sorted(candidates, key=application_sort_key)
    base_text = text
    lines = []

    def card(row):
        score = row.get("match_score")
        ai = (
            f"Tier A · {row['recommendation']} · confidence: {row['score_confidence']} · {score}/100"
            if row["review_tier"] == "A"
            else "Tier B · UNSCORED"
        )
        if row.get("provisional") and score is not None:
            ai += " (provisional)"
        url = row["url"] if len(row["url"]) <= 180 else "Open vacancy using the button below"
        return (
            f"\n\n{row['title'][:85]} · {row['company'][:40]}\n{ai}\n"
            f"Priority: {row['application_priority']} · Hours: {row['hours_fit']}\n"
            f"Seniority: {row['seniority_fit']} · JD: {row['jd_status']}\n"
            f"{row['eligibility_summary'][:85]}\n{url}"
        )

    def fits(parts):
        return len((base_text + "".join(parts)).encode("utf-16-le")) // 2 <= 3800

    for row in ordered:
        line = card(row)
        if len(selected) >= 8:
            break
        if not fits(lines + [line]):
            continue
        lines.append(line)
        selected.append(row)
    # Acknowledge only selected identities; reserve the final fitting slot for an
    # old pending item so continuous new HIGH jobs cannot starve existing backlog.
    from job_radar.eligibility import _stamp

    age_limit = (
        datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()
        - load_policy()["notification_aging_days"] * 86400
    )
    waiting = [row for row in ordered if _stamp(row.get("first_seen_at")) <= age_limit]
    oldest = (
        min(waiting, key=lambda row: (_stamp(row.get("first_seen_at")), row["dedupe_key"]))
        if waiting
        else None
    )
    if oldest is not None and oldest not in selected:
        line = card(oldest)
        while selected and (len(selected) >= 8 or not fits(lines + [line])):
            selected.pop()
            lines.pop()
        if fits(lines + [line]):
            selected.append(oldest)
            lines.append(line)
    text = base_text + "".join(lines)
    for row in selected:
        buttons.append(
            [
                {"text": "Open", "url": row["url"]},
                {
                    "text": f"Applied: {row['title'][:25]}",
                    "callback_data": f"applyjob:{row['application_key']}",
                },
                {"text": "Skip", "callback_data": f"skipjob:{row['application_key']}"},
            ]
        )
    (output / "telegram-preview.txt").write_text(text)
    report["preview_items"] = len(selected)
    report["preview_tiers"] = {
        tier: sum(row["review_tier"] == tier for row in selected) for tier in ("A", "B")
    }
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
    enrichment_client=None,
    enrichment_limit=5,
    score_model=None,
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
        new_source_keys = set()
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
                            new_source_keys.update(result.inserted_keys)
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
                    new_source_keys.update(result.inserted_keys)
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
            model = provider.model if provider else score_model
            rows = review_rows(repository, profile_hash, model, profile)
            new_keys = {
                row["semantic_key"]
                for row in rows
                if new_source_keys.intersection(row["source_keys"])
            }
            if enrichment_client is not None and enrichment_limit:
                try:
                    progress(
                        f"Enriching eligible vacancies within {enrichment_limit} detail requests..."
                    )
                    report["enrichment"] = _enrich_review(
                        repository,
                        rows,
                        enrichment_client,
                        enrichment_limit,
                        profile,
                        model,
                        new_keys,
                        run_id,
                    )
                    incomplete = sum(
                        report["enrichment"].get(key, 0)
                        for key in ("snippet_only", "failed", "blocked", "empty")
                    )
                    report["enrichment"]["status"] = "partial" if incomplete else "success"
                    repository.record_run_stage(
                        run_id,
                        "enrichment",
                        status=report["enrichment"]["status"],
                        item_count=report["enrichment"]["requests"],
                        error=f"{incomplete} incomplete JDs; provisional fallback"
                        if incomplete
                        else "",
                    )
                except Exception as error:
                    report["errors"].append(f"Enrichment: {error_label(error)}")
                    repository.record_run_stage(
                        run_id, "enrichment", status="failed", error=error_label(error)
                    )
                rows = review_rows(repository, profile_hash, model, profile)
            score_keys = {row["semantic_key"] for row in rows if _scoreable(row)}
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
                            only=None,
                            limit=score_limit,
                            retry_failed=score_retry_failed,
                            semantic_keys=score_keys,
                            retry_after_seconds=86400,
                            input_cost_per_million=input_cost_per_million,
                            output_cost_per_million=output_cost_per_million,
                            new_semantic_keys=new_keys,
                            fairness_turn=run_id,
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
            rows = review_rows(repository, profile_hash, model, profile)
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
        tier_a, tier_b = review_tiers(rows)
        shortlisted = tier_a + tier_b
        report["review_tiers"] = {"ai_recommended": len(tier_a), "awaiting_ai": len(tier_b)}
        report["shortlisted"] = len(shortlisted)
        report["top_applications"] = [
            {
                key: row.get(key)
                for key in (
                    "dedupe_key",
                    "title",
                    "company",
                    "url",
                    "review_tier",
                    "technical_match_score",
                    "application_priority",
                    "hours_fit",
                    "seniority_fit",
                    "location_fit",
                    "eligibility_summary",
                    "evidence_quality",
                )
            }
            for row in sorted(shortlisted, key=application_sort_key)[:5]
        ]
        try:
            _deliver(repository, report, shortlisted, telegram, chat_id, day, output)
        except Exception as error:
            report["errors"].append(f"Digest: {error_label(error)}")
            report["telegram"] = "failed" if telegram else "disabled"
        report["funnel"] = _funnel(repository, rows, tier_a, tier_b, chat_id, report)
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
