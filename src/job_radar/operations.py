"""Local preflight and read-only operational health; never returns secret values."""

import importlib.util
import fcntl
import os
import sqlite3
from pathlib import Path

from job_radar.searches import load_searches


def writable_parent(path: Path) -> bool:
    while not path.exists() and path != path.parent:
        path = path.parent
    return path.is_dir() and os.access(path, os.W_OK | os.X_OK)


def health(
    settings, database: Path, searches: Path, output: Path, *, require_telegram=False, owner_id=None
):
    errors, warnings = [], []
    report = {"errors": errors, "warnings": warnings}
    if (
        not writable_parent(database.parent)
        or database.is_dir()
        or (database.exists() and not os.access(database, os.W_OK))
    ):
        errors.append("database_path_unwritable")
    if not writable_parent(output) or (output.exists() and not output.is_dir()):
        errors.append("output_path_unwritable")
    try:
        profiles = load_searches(searches)
        report["sources"] = [{"name": p.name, "source": p.source} for p in profiles]
    except Exception:
        errors.append("search_configuration_invalid")
    if database.is_file():
        try:
            with sqlite3.connect(
                database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5
            ) as c:
                c.row_factory = sqlite3.Row
                report["integrity"] = c.execute("PRAGMA quick_check").fetchone()[0]
                if report["integrity"] != "ok":
                    errors.append("database_integrity_failed")
                names = {
                    r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                required = {
                    "jobs",
                    "run_history",
                    "source_cooldowns",
                    "notification_outbox",
                    "enrichment_attempts",
                    "ai_budget_reservations",
                }
                if not required <= names:
                    errors.append("schema_upgrade_required_run_init_db")
                else:
                    row = c.execute(
                        "SELECT id,status,ended_at,error FROM run_history ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    report["last_run"] = (
                        {k: row[k] for k in ("id", "status", "ended_at")} if row else None
                    )
                    report["last_success"] = dict(
                        c.execute(
                            "SELECT id,ended_at FROM run_history WHERE status='success' ORDER BY id DESC LIMIT 1"
                        ).fetchone()
                        or {}
                    )
                    report["recent_failures"] = [
                        dict(r)
                        for r in c.execute(
                            "SELECT id,status FROM run_history WHERE status IN ('partial','failed','interrupted') ORDER BY id DESC LIMIT 5"
                        )
                    ]
                    report["source_cooldowns"] = [
                        dict(r) for r in c.execute("SELECT * FROM source_cooldowns")
                    ]
                    report["uncertain_notifications"] = c.execute(
                        "SELECT COUNT(*) FROM notification_outbox WHERE status='unknown'"
                    ).fetchone()[0]
        except sqlite3.Error:
            errors.append("database_unreadable")
    else:
        errors.append("database_missing_run_init_db")
    try:
        owner = owner_id if owner_id is not None else int(os.getenv("TELEGRAM_OWNER_ID", "0"))
    except ValueError:
        owner = 0
    telegram_ok = bool(
        settings.telegram_bot_token and owner > 0 and str(owner) == settings.telegram_chat_id
    )
    report["telegram_configured"] = telegram_ok
    if not telegram_ok:
        (errors if require_telegram else warnings).append("telegram_owner_or_token_not_configured")
    for package in ("yaml", "bs4", "openpyxl"):
        if importlib.util.find_spec(package) is None:
            errors.append("missing_dependency_" + package)
    report["ai_enabled"] = False
    if settings.openai_api_key:
        try:
            from job_radar.ai.budget import request_ceiling
            from job_radar.candidate import load_candidate_profile

            profile = load_candidate_profile(settings.candidate_profile_path)
            ceiling = request_ceiling(settings)
            if not any(profile.get(k) for k in ("education", "projects", "experience")):
                raise ValueError()
            if min(settings.ai_run_budget_usd, settings.ai_day_budget_usd) < ceiling:
                raise ValueError()
            report["ai_enabled"] = True
        except Exception:
            warnings.append("ai_full_search_unavailable_check_profile_limits_and_prices")
    report["collection_running"] = False
    lock = database.with_suffix(database.suffix + ".run.lock")
    try:
        with lock.open("r") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                report["collection_running"] = True
    except FileNotFoundError:
        pass
    except OSError:
        warnings.append("collection_lock_unreadable")
    report["status"] = "failed" if errors else "ok"
    return report
