import json
import hashlib
import sqlite3
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

from job_radar.models import Job, semantic_key
from job_radar.screening import load_rules, screen_job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    dedupe_key TEXT PRIMARY KEY,
    semantic_key TEXT NOT NULL,
    source TEXT NOT NULL,
    source_job_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT NOT NULL,
    snippet TEXT NOT NULL,
    url TEXT NOT NULL,
    email_message_id TEXT NOT NULL,
    email_received_at TEXT NOT NULL,
    alert_subject TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    screening_status TEXT NOT NULL DEFAULT 'review',
    rejection_reasons TEXT NOT NULL DEFAULT '[]',
    screening_rules_version TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    description_language TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL DEFAULT '',
    fetch_status TEXT NOT NULL DEFAULT '',
    fetch_error TEXT NOT NULL DEFAULT '',
    final_url TEXT NOT NULL DEFAULT '',
    description_hash TEXT NOT NULL DEFAULT '',
    description_confidence TEXT NOT NULL DEFAULT '',
    http_status INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS source_cooldowns (
    source TEXT PRIMARY KEY, next_attempt_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_budget_reservations (
    id INTEGER PRIMARY KEY, day TEXT NOT NULL, run_key TEXT NOT NULL,
    reserved_usd REAL NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ai_budget_day ON ai_budget_reservations(day);
CREATE INDEX IF NOT EXISTS idx_ai_budget_run ON ai_budget_reservations(run_key);
CREATE TABLE IF NOT EXISTS enrichment_attempts (
    url TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_status TEXT NOT NULL DEFAULT '',
    last_attempt_at TEXT NOT NULL DEFAULT '',
    next_retry_at TEXT NOT NULL DEFAULT '',
    latency_ms REAL NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS enrichment_cache (
    url TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    description_language TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL,
    fetch_status TEXT NOT NULL,
    fetch_error TEXT NOT NULL DEFAULT '',
    final_url TEXT NOT NULL DEFAULT '',
    description_hash TEXT NOT NULL DEFAULT '',
    description_confidence TEXT NOT NULL DEFAULT '',
    http_status INTEGER
);
CREATE TABLE IF NOT EXISTS application_packages (
    cache_key TEXT PRIMARY KEY,
    semantic_key TEXT NOT NULL,
    canonical_dedupe_key TEXT NOT NULL,
    candidate_profile_hash TEXT NOT NULL,
    description_hash TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    provisional INTEGER NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    response_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS job_scores (
    score_cache_key TEXT PRIMARY KEY,
    semantic_key TEXT NOT NULL,
    canonical_dedupe_key TEXT NOT NULL,
    candidate_profile_hash TEXT NOT NULL,
    description_hash TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    provisional INTEGER NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    response_id TEXT NOT NULL DEFAULT '',
    score_status TEXT NOT NULL,
    score_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS application_drafts (
    draft_cache_key TEXT PRIMARY KEY,
    score_cache_key TEXT NOT NULL,
    semantic_key TEXT NOT NULL,
    canonical_dedupe_key TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    response_id TEXT NOT NULL DEFAULT '',
    draft_status TEXT NOT NULL,
    draft_error TEXT NOT NULL DEFAULT '',
    rewrite_attempted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS telegram_deliveries (
    semantic_key TEXT PRIMARY KEY,
    canonical_dedupe_key TEXT NOT NULL DEFAULT '',
    message_id INTEGER NOT NULL,
    delivered_at TEXT NOT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'delivered',
    applied_at TEXT NOT NULL DEFAULT '',
    skipped_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'delivered',
    error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS telegram_state (
    state_key TEXT PRIMARY KEY,
    state_value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    days INTEGER NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 0,
    no_telegram INTEGER NOT NULL DEFAULT 0,
    counts_json TEXT NOT NULL DEFAULT '{}',
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS run_stage_history (
    run_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, stage),
    FOREIGN KEY (run_id) REFERENCES run_history(id)
);
CREATE TABLE IF NOT EXISTS run_item_status (
    run_id INTEGER NOT NULL,
    item_key TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, item_key, stage),
    FOREIGN KEY (run_id) REFERENCES run_history(id)
);
CREATE TABLE IF NOT EXISTS job_observations (
    dedupe_key TEXT NOT NULL,
    connector TEXT NOT NULL,
    search_url TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (dedupe_key, connector, search_url, content_hash)
);
CREATE TABLE IF NOT EXISTS applications (
    dedupe_key TEXT PRIMARY KEY,
    semantic_key TEXT NOT NULL,
    state TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS application_events (
    request_id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL,
    state TEXT NOT NULL,
    actor TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_outbox (
    notification_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    message_id INTEGER,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_alert_items (
    chat_hash TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    semantic_key TEXT NOT NULL,
    notification_key TEXT NOT NULL,
    PRIMARY KEY (chat_hash, dedupe_key),
    FOREIGN KEY (notification_key) REFERENCES notification_outbox(notification_key)
);
"""

MIGRATION_COLUMNS = {
    "observed_at": "TEXT NOT NULL DEFAULT ''",
    "connector": "TEXT NOT NULL DEFAULT ''",
    "search_url": "TEXT NOT NULL DEFAULT ''",
    "published_at": "TEXT NOT NULL DEFAULT ''",
    "semantic_key": "TEXT NOT NULL DEFAULT ''",
    "screening_status": "TEXT NOT NULL DEFAULT 'review'",
    "rejection_reasons": "TEXT NOT NULL DEFAULT '[]'",
    "screening_rules_version": "TEXT NOT NULL DEFAULT ''",
    "description": "TEXT NOT NULL DEFAULT ''",
    "description_language": "TEXT NOT NULL DEFAULT ''",
    "fetched_at": "TEXT NOT NULL DEFAULT ''",
    "fetch_status": "TEXT NOT NULL DEFAULT ''",
    "fetch_error": "TEXT NOT NULL DEFAULT ''",
    "final_url": "TEXT NOT NULL DEFAULT ''",
    "description_hash": "TEXT NOT NULL DEFAULT ''",
    "description_confidence": "TEXT NOT NULL DEFAULT ''",
    "http_status": "INTEGER",
}

CACHE_MIGRATION_COLUMNS = {
    "description_confidence": "TEXT NOT NULL DEFAULT ''",
    "http_status": "INTEGER",
}

TELEGRAM_MIGRATION_COLUMNS = {
    "canonical_dedupe_key": "TEXT NOT NULL DEFAULT ''",
    "delivery_status": "TEXT NOT NULL DEFAULT 'delivered'",
    "applied_at": "TEXT NOT NULL DEFAULT ''",
    "skipped_at": "TEXT NOT NULL DEFAULT ''",
    "status": "TEXT NOT NULL DEFAULT 'delivered'",
    "error": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
}


@dataclass(frozen=True, slots=True)
class UpsertResult:
    inserted_keys: tuple[str, ...]
    changed_keys: tuple[str, ...]
    unchanged_keys: tuple[str, ...]
    affected_semantic_keys: tuple[str, ...]

    @property
    def inserted(self) -> int:
        return len(self.inserted_keys)

    @property
    def changed(self) -> int:
        return len(self.changed_keys)

    @property
    def unchanged(self) -> int:
        return len(self.unchanged_keys)


SOURCE_FIELDS = (
    "semantic_key",
    "title",
    "company",
    "location",
    "snippet",
    "url",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            return datetime.min.replace(tzinfo=timezone.utc)
    return (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )


class JobRepository:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        try:
            # Serialize schema inspection and additive migration across processes.
            self.connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
            self._migrate()
        except BaseException:
            self.connection.close()
            raise

    def __enter__(self) -> "JobRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _migrate(self) -> None:
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(jobs)")}
        for name, definition in MIGRATION_COLUMNS.items():
            if name not in columns:
                self.connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
        cache_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(enrichment_cache)")
        }
        for name, definition in CACHE_MIGRATION_COLUMNS.items():
            if name not in cache_columns:
                self.connection.execute(
                    f"ALTER TABLE enrichment_cache ADD COLUMN {name} {definition}"
                )
        telegram_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(telegram_deliveries)")
        }
        for name, definition in TELEGRAM_MIGRATION_COLUMNS.items():
            if name not in telegram_columns:
                self.connection.execute(
                    f"ALTER TABLE telegram_deliveries ADD COLUMN {name} {definition}"
                )
        # Copy legacy status once into a newly added column. Re-running a bidirectional
        # update on open could erase an Applied value when the old status was Delivered.
        if "delivery_status" not in telegram_columns and "status" in telegram_columns:
            self.connection.execute("UPDATE telegram_deliveries SET delivery_status=status "
                                    "WHERE status IN ('delivered','applied','skipped','failed')")
        elif "status" not in telegram_columns and "delivery_status" in telegram_columns:
            self.connection.execute("UPDATE telegram_deliveries SET status=delivery_status "
                                    "WHERE delivery_status IN ('delivered','applied','skipped','failed')")
        self.connection.execute(
            """
            UPDATE jobs
            SET http_status = CAST(substr(fetch_error, 6) AS INTEGER)
            WHERE http_status IS NULL AND fetch_error GLOB 'HTTP [0-9]*'
            """
        )
        self.connection.execute(
            """
            UPDATE enrichment_cache
            SET http_status = CAST(substr(fetch_error, 6) AS INTEGER)
            WHERE http_status IS NULL AND fetch_error GLOB 'HTTP [0-9]*'
            """
        )
        self.connection.execute(
            """
            UPDATE jobs
            SET fetch_error = COALESCE(
                (
                    SELECT CASE enrichment_cache.fetch_status
                        WHEN 'expired' THEN 'expired listing'
                        WHEN 'blocked' THEN 'blocked page'
                        WHEN 'empty' THEN 'empty description'
                        WHEN 'failed' THEN 'fetch failed'
                        ELSE ''
                    END
                    FROM enrichment_cache
                    WHERE enrichment_cache.url = jobs.url
                ),
                ''
            )
            WHERE fetch_status = 'snippet_only' AND fetch_error = ''
            """
        )
        self.connection.execute(
            """
            UPDATE jobs
            SET http_status = 200
            WHERE http_status IS NULL
              AND fetch_status IN ('success', 'expired', 'empty')
            """
        )
        self.connection.execute(
            """
            UPDATE enrichment_cache
            SET http_status = 200
            WHERE http_status IS NULL
              AND fetch_status IN ('success', 'expired', 'empty')
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_semantic_key ON jobs(semantic_key)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_screening_status ON jobs(screening_status)"
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_application_packages_semantic
            ON application_packages(semantic_key, created_at DESC)
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_job_scores_semantic
            ON job_scores(semantic_key, created_at DESC)
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_application_drafts_score
            ON application_drafts(score_cache_key, created_at DESC)
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_run_item_status_item
            ON run_item_status(item_key, stage, updated_at DESC)
            """
        )
        rows = self.connection.execute(
            """
            SELECT dedupe_key, title, company, location
            FROM jobs
            WHERE semantic_key = ''
            """
        ).fetchall()
        for row in rows:
            self.connection.execute(
                "UPDATE jobs SET semantic_key = ? WHERE dedupe_key = ?",
                (
                    semantic_key(row["title"], row["company"], row["location"], row["dedupe_key"]),
                    row["dedupe_key"],
                ),
            )
        self._migrate_legacy_application_packages()
        self.connection.commit()

    def _migrate_legacy_application_packages(self) -> None:
        legacy_rows = self.connection.execute(
            "SELECT * FROM application_packages ORDER BY created_at"
        ).fetchall()
        for row in legacy_rows:
            exists = self.connection.execute(
                "SELECT 1 FROM job_scores WHERE score_cache_key = ?",
                (row["cache_key"],),
            ).fetchone()
            if exists:
                continue

            result: dict[str, Any] = {}
            if row["status"] == "success":
                try:
                    package = json.loads(row["result_json"])
                except json.JSONDecodeError:
                    package = {}
                score_fields = (
                    "match_score",
                    "score_confidence",
                    "recommendation",
                    "matching_strengths",
                    "missing_requirements",
                    "risk_flags",
                    "role_summary",
                )
                if all(field in package for field in score_fields):
                    result = {field: package[field] for field in score_fields}
                    result["provisional"] = bool(row["provisional"])

            score_status = "success" if result else "failed"
            score_error = "" if result else row["error"]
            self.connection.execute(
                """
                INSERT INTO job_scores (
                    score_cache_key, semantic_key, canonical_dedupe_key,
                    candidate_profile_hash, description_hash, prompt_version,
                    model, provisional, result_json, input_tokens, output_tokens,
                    total_tokens, estimated_cost_usd, response_id, score_status,
                    score_error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["cache_key"],
                    row["semantic_key"],
                    row["canonical_dedupe_key"],
                    row["candidate_profile_hash"],
                    row["description_hash"],
                    row["prompt_version"],
                    row["model"],
                    row["provisional"],
                    json.dumps(result, ensure_ascii=False),
                    row["input_tokens"],
                    row["output_tokens"],
                    row["total_tokens"],
                    row["estimated_cost_usd"],
                    row["response_id"],
                    score_status,
                    score_error,
                    row["created_at"],
                ),
            )

            if score_status != "success":
                continue
            package = json.loads(row["result_json"])
            recommendation = result["recommendation"]
            if recommendation == "skip":
                draft_status = "not_applicable"
                draft_result: dict[str, Any] = {}
            else:
                draft_result = {
                    "cover_letter_language": package.get("cover_letter_language", "unknown"),
                    "cover_letter": package.get("cover_letter", ""),
                    "recruiter_message": package.get("recruiter_message", ""),
                }
                draft_status = "ready" if draft_result["cover_letter"] else "failed"
            self.connection.execute(
                """
                INSERT OR IGNORE INTO application_drafts (
                    draft_cache_key, score_cache_key, semantic_key,
                    canonical_dedupe_key, prompt_version, model, result_json,
                    draft_status, draft_error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"legacy-{row['cache_key']}",
                    row["cache_key"],
                    row["semantic_key"],
                    row["canonical_dedupe_key"],
                    "legacy-package-v1",
                    row["model"],
                    json.dumps(draft_result, ensure_ascii=False),
                    draft_status,
                    "" if draft_status != "failed" else "Legacy draft was empty",
                    row["created_at"],
                ),
            )

    def classify(self, jobs: Iterable[Job]) -> UpsertResult:
        inserted: list[str] = []
        changed: list[str] = []
        unchanged: list[str] = []
        affected: set[str] = set()
        for job in self._merge_inputs(jobs):
            row = self.connection.execute(
                "SELECT * FROM jobs WHERE dedupe_key = ?", (job.dedupe_key,)
            ).fetchone()
            values = job.to_dict()
            if row is None:
                inserted.append(job.dedupe_key)
                affected.add(job.semantic_key)
            elif any(str(row[field]) != str(values[field]) for field in SOURCE_FIELDS):
                changed.append(job.dedupe_key)
                affected.update((str(row["semantic_key"]), job.semantic_key))
            else:
                unchanged.append(job.dedupe_key)
        return UpsertResult(
            tuple(inserted),
            tuple(changed),
            tuple(unchanged),
            tuple(sorted(affected)),
        )

    def upsert_detailed(self, jobs: Iterable[Job]) -> UpsertResult:
        items = self._merge_inputs(jobs)
        result = self.classify(items)
        mutable = set(result.inserted_keys) | set(result.changed_keys)
        for job in items:
            values = job.to_dict()
            if job.dedupe_key not in mutable:
                self.connection.execute(
                    """
                    UPDATE jobs
                    SET email_message_id = :email_message_id,
                        email_received_at = :email_received_at,
                        alert_subject = :alert_subject,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE dedupe_key = :dedupe_key
                    """,
                    values,
                )
                self.connection.execute(
                    """UPDATE jobs SET observed_at = MAX(observed_at, :observed_at),
                    connector = :connector, search_url = :search_url,
                    published_at = :published_at WHERE dedupe_key = :dedupe_key""",
                    values,
                )
                continue
            self.connection.execute(
                """
                INSERT INTO jobs (
                    dedupe_key, semantic_key, source, source_job_id, title, company, location,
                    snippet, url, email_message_id, email_received_at, alert_subject,
                    observed_at, connector, search_url, published_at
                ) VALUES (
                    :dedupe_key, :semantic_key, :source, :source_job_id, :title,
                    :company, :location,
                    :snippet, :url, :email_message_id, :email_received_at, :alert_subject,
                    :observed_at, :connector, :search_url, :published_at
                )
                ON CONFLICT(dedupe_key) DO UPDATE SET
                    semantic_key = excluded.semantic_key,
                    title = excluded.title,
                    company = excluded.company,
                    location = excluded.location,
                    snippet = excluded.snippet,
                    url = excluded.url,
                    email_message_id = excluded.email_message_id,
                    email_received_at = excluded.email_received_at,
                    alert_subject = excluded.alert_subject,
                    observed_at = excluded.observed_at,
                    connector = excluded.connector,
                    search_url = excluded.search_url,
                    published_at = excluded.published_at,
                    screening_status = 'review',
                    rejection_reasons = '[]',
                    screening_rules_version = '',
                    fetched_at = '',
                    updated_at = CURRENT_TIMESTAMP
                """,
                values,
            )
        self.connection.commit()
        return result

    def _merge_inputs(self, jobs: Iterable[Job]) -> list[Job]:
        merged: dict[str, Job] = {}
        for job in jobs:
            row = merged.get(job.dedupe_key)
            if row is not None:
                previous = row.to_dict()
            else:
                stored = self.connection.execute(
                    "SELECT * FROM jobs WHERE dedupe_key = ?", (job.dedupe_key,)
                ).fetchone()
                previous = dict(stored) if stored else {}
            updates = {}
            for field in fields(Job):
                value = getattr(job, field.name)
                if not value and previous.get(field.name):
                    updates[field.name] = previous[field.name]
            if job.email_message_id and previous:
                newer_than_email = _timestamp(job.email_received_at) >= _timestamp(
                    previous.get("email_received_at", "")
                )
                newer_than_direct = previous.get("connector", "gmail") in {
                    "",
                    "gmail",
                } or _timestamp(job.email_received_at) >= _timestamp(
                    previous.get("observed_at", "")
                )
                if not newer_than_email or not newer_than_direct:
                    for key in ("title", "company", "location", "snippet", "url", "connector"):
                        if previous.get(key):
                            updates[key] = previous[key]
                if not newer_than_email:
                    for key in ("email_message_id", "email_received_at", "alert_subject"):
                        updates[key] = previous.get(key, "")
            merged[job.dedupe_key] = replace(job, **updates)
        return list(merged.values())

    def record_observation(self, job: Job) -> None:
        values = job.to_dict()
        payload = json.dumps({key: values[key] for key in SOURCE_FIELDS}, sort_keys=True)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        now = job.observed_at or _now()
        self.connection.execute(
            """INSERT INTO job_observations VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dedupe_key, connector, search_url, content_hash)
            DO UPDATE SET last_seen_at = MAX(last_seen_at, excluded.last_seen_at)""",
            (job.dedupe_key, job.connector or "gmail", job.search_url, digest, payload, now, now),
        )
        self.connection.commit()

    def set_application(
        self, key: str, state: str, request_id: str, actor="cli", note="", expected_revision=None
    ):
        with self.connection:
            if not self.connection.in_transaction:
                self.connection.execute("BEGIN IMMEDIATE")
            return self._set_application_locked(
                key, state, request_id, actor, note, expected_revision
            )

    def _set_application_locked(self, key, state, request_id, actor, note, expected_revision):
        states = {
            "not_applied",
            "saved",
            "applied",
            "interview",
            "offer",
            "rejected",
            "withdrawn",
            "skipped",
        }
        if state not in states:
            raise ValueError("Unsupported application state")
        replay = self.connection.execute(
            "SELECT dedupe_key, state FROM application_events WHERE request_id = ?", (request_id,)
        ).fetchone()
        if replay:
            if tuple(replay) != (key, state):
                raise ValueError("Request ID was already used for a different action")
            return "replay"
        job = self.connection.execute(
            "SELECT semantic_key FROM jobs WHERE dedupe_key = ?", (key,)
        ).fetchone()
        if not job:
            raise ValueError("Unknown vacancy ID")
        current = self.connection.execute(
            "SELECT revision FROM applications WHERE dedupe_key = ?", (key,)
        ).fetchone()
        revision = current[0] if current else 0
        if expected_revision is not None and expected_revision != revision:
            raise ValueError("Application revision conflict")
        with self.connection:
            self.connection.execute(
                "INSERT INTO application_events VALUES (?, ?, ?, ?, ?, ?)",
                (request_id, key, state, actor, note, _now()),
            )
            self.connection.execute(
                """INSERT INTO applications VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(dedupe_key) DO UPDATE SET state=excluded.state,
                revision=excluded.revision, updated_at=excluded.updated_at""",
                (key, job[0], state, revision + 1, _now()),
            )
        return "updated"

    def application_for(self, key: str) -> dict[str, Any]:
        row = self.connection.execute(
            """SELECT * FROM applications WHERE dedupe_key = ? OR semantic_key IN
            (SELECT semantic_key FROM jobs WHERE dedupe_key = ?)
            ORDER BY updated_at DESC LIMIT 1""",
            (key, key),
        ).fetchone()
        if row:
            return dict(row)
        # Preserve access to pre-ledger Applied/Skip actions.
        legacy = self.connection.execute(
            """SELECT delivery_status FROM telegram_deliveries WHERE semantic_key IN
            (SELECT semantic_key FROM jobs WHERE dedupe_key = ?)""",
            (key,),
        ).fetchone()
        return {
            "state": legacy[0] if legacy and legacy[0] in {"applied", "skipped"} else "not_applied",
            "revision": 0,
        }

    def upsert(self, jobs: Iterable[Job]) -> tuple[int, int]:
        result = self.upsert_detailed(jobs)
        return result.inserted, result.changed

    def screen_keys(self, dedupe_keys: Iterable[str]) -> dict[str, int]:
        keys = tuple(dict.fromkeys(dedupe_keys))
        counts = {"eligible": 0, "review": 0, "rejected": 0}
        if not keys:
            return counts
        placeholders = ",".join("?" for _ in keys)
        rows = self.connection.execute(
            f"""
            SELECT dedupe_key, title, company, location, snippet,
                   fetch_status, fetch_error, description, http_status
            FROM jobs WHERE dedupe_key IN ({placeholders})
            """,
            keys,
        ).fetchall()
        rules = load_rules()
        from job_radar.availability import is_expired

        for row in rows:
            result = screen_job(
                row["title"],
                row["location"],
                row["description"] or row["snippet"],
                rules=rules,
                fetch_status="expired" if is_expired(dict(row)) else row["fetch_status"],
                fetch_error=row["fetch_error"],
            )
            self.connection.execute(
                """
                UPDATE jobs
                SET semantic_key = ?, screening_status = ?, rejection_reasons = ?,
                    screening_rules_version = ?, updated_at = CURRENT_TIMESTAMP
                WHERE dedupe_key = ?
                """,
                (
                    semantic_key(row["title"], row["company"], row["location"], row["dedupe_key"]),
                    result.status,
                    json.dumps(result.rejection_reasons, ensure_ascii=False),
                    result.rules_version,
                    row["dedupe_key"],
                ),
            )
            counts[result.status] += 1
        self.connection.commit()
        return counts

    def screen_all(self) -> dict[str, int]:
        keys = [row["dedupe_key"] for row in self.connection.execute("SELECT dedupe_key FROM jobs")]
        return self.screen_keys(keys)

    def all(
        self, only: str | None = None, require_description: bool = False
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM jobs"
        conditions: list[str] = []
        parameters: tuple[str, ...] = ()
        if only:
            conditions.append("screening_status = ?")
            parameters = (only,)
        if require_description:
            conditions.append("fetch_status IN ('success', 'snippet_only') AND description != ''")
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY email_received_at DESC, created_at DESC"
        rows = self.connection.execute(query, parameters).fetchall()
        results = [dict(row) for row in rows]
        for result in results:
            result["rejection_reasons"] = json.loads(result["rejection_reasons"])
        return results

    def source_cooldown(self, source: str) -> str | None:
        row = self.connection.execute("SELECT next_attempt_at FROM source_cooldowns WHERE source=?", (source,)).fetchone()
        return row[0] if row and row[0] > _now() else None

    def defer_source(self, source: str, seconds: float) -> None:
        from datetime import datetime, timedelta, timezone
        stamp = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO source_cooldowns VALUES (?,?)", (source, stamp))

    def enrichment_attempt(self, url: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM enrichment_attempts WHERE url = ?", (url,)
        ).fetchone()
        return dict(row) if row else None

    def record_enrichment_attempt(self, url: str, status: str, latency_ms: float,
                                  metadata: dict[str, Any], retry_hours: float) -> None:
        from datetime import datetime, timedelta, timezone

        previous = self.enrichment_attempt(url) or {}
        attempts = 0 if status == "success" else int(previous.get("attempts", 0)) + 1
        now = datetime.now(timezone.utc)
        delay = retry_hours * min(2 ** max(0, attempts - 1), 8)
        if status in {"expired", "blocked"}:
            delay = max(delay, 168)
        # Successful metadata survives a failed refresh, just like description text.
        saved = json.dumps(metadata, ensure_ascii=False) if metadata else previous.get("metadata_json", "{}")
        self.connection.execute(
            "INSERT OR REPLACE INTO enrichment_attempts VALUES (?, ?, ?, ?, ?, ?, ?)",
            (url, attempts, status, now.isoformat(),
             (now + timedelta(hours=delay)).isoformat(), latency_ms, saved),
        )
        self.connection.commit()

    def cached_enrichment(self, url: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM enrichment_cache WHERE url = ?", (url,)
        ).fetchone()
        return dict(row) if row else None

    def cache_enrichment(self, url: str, values: dict[str, Any]) -> None:
        previous = self.cached_enrichment(url)
        if (previous and previous["fetch_status"] == "success" and previous["description"]
                and values.get("fetch_status") not in {"success", "expired"}):
            # Attempt state records the failure without destroying the last good cache.
            values = {**values, **{key: previous[key] for key in (
                "description", "description_language", "fetched_at", "fetch_status",
                "description_hash", "description_confidence", "final_url",
            )}}
        complete_values = {
            "description_confidence": "",
            "http_status": None,
            **values,
        }
        self.connection.execute(
            """
            INSERT INTO enrichment_cache (
                url, description, description_language, fetched_at, fetch_status,
                fetch_error, final_url, description_hash, description_confidence,
                http_status
            ) VALUES (
                :url, :description, :description_language, :fetched_at, :fetch_status,
                :fetch_error, :final_url, :description_hash, :description_confidence,
                :http_status
            )
            ON CONFLICT(url) DO UPDATE SET
                description = excluded.description,
                description_language = excluded.description_language,
                fetched_at = excluded.fetched_at,
                fetch_status = excluded.fetch_status,
                fetch_error = excluded.fetch_error,
                final_url = excluded.final_url,
                description_hash = excluded.description_hash,
                description_confidence = excluded.description_confidence,
                http_status = excluded.http_status
            """,
            {"url": url, **complete_values},
        )
        self.connection.commit()

    def update_enrichment(self, dedupe_key: str, values: dict[str, Any]) -> None:
        previous = self.connection.execute("SELECT * FROM jobs WHERE dedupe_key=?", (dedupe_key,)).fetchone()
        if (previous and previous["fetch_status"] == "success" and previous["description"]
                and values.get("fetch_status") != "success"):
            # Preserve verified content after snippets, walls and failed re-fetches.
            fields = ("description", "description_language", "description_hash", "description_confidence")
            values = {**values, **{key: previous[key] for key in fields}}
            if values.get("fetch_status") != "expired":
                values.update(fetch_status="success", fetched_at=previous["fetched_at"],
                              fetch_error="Refresh failed: " + str(values.get("fetch_status", "unknown")))
        complete_values = {
            "description_confidence": "",
            "http_status": None,
            **values,
        }
        self.connection.execute(
            """
            UPDATE jobs
            SET description = :description,
                description_language = :description_language,
                fetched_at = :fetched_at,
                fetch_status = :fetch_status,
                fetch_error = :fetch_error,
                final_url = :final_url,
                description_hash = :description_hash,
                description_confidence = :description_confidence,
                http_status = :http_status,
                updated_at = CURRENT_TIMESTAMP
            WHERE dedupe_key = :dedupe_key
            """,
            {"dedupe_key": dedupe_key, **complete_values},
        )
        self.connection.commit()

    def cached_application_package(self, cache_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM application_packages WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        return dict(row) if row else None

    def save_application_package(self, values: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO application_packages (
                cache_key, semantic_key, canonical_dedupe_key,
                candidate_profile_hash, description_hash, prompt_version,
                model, provisional, result_json, input_tokens, output_tokens,
                total_tokens, estimated_cost_usd, response_id, status, error,
                created_at
            ) VALUES (
                :cache_key, :semantic_key, :canonical_dedupe_key,
                :candidate_profile_hash, :description_hash, :prompt_version,
                :model, :provisional, :result_json, :input_tokens, :output_tokens,
                :total_tokens, :estimated_cost_usd, :response_id, :status, :error,
                :created_at
            )
            ON CONFLICT(cache_key) DO UPDATE SET
                semantic_key = excluded.semantic_key,
                canonical_dedupe_key = excluded.canonical_dedupe_key,
                result_json = excluded.result_json,
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                total_tokens = excluded.total_tokens,
                estimated_cost_usd = excluded.estimated_cost_usd,
                response_id = excluded.response_id,
                status = excluded.status,
                error = excluded.error,
                created_at = excluded.created_at,
                updated_at = CURRENT_TIMESTAMP
            """,
            values,
        )
        self.connection.commit()

    def application_packages(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            WITH ranked AS (
                SELECT application_packages.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY semantic_key
                           ORDER BY created_at DESC, cache_key DESC
                       ) AS package_rank
                FROM application_packages
                WHERE status = 'success'
            )
            SELECT ranked.*, jobs.title, jobs.company, jobs.location,
                   jobs.url, jobs.final_url
            FROM ranked
            JOIN jobs ON jobs.dedupe_key = ranked.canonical_dedupe_key
            WHERE ranked.package_rank = 1
            ORDER BY json_extract(ranked.result_json, '$.match_score') DESC,
                     ranked.created_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def cached_job_score(self, score_cache_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM job_scores WHERE score_cache_key = ?",
            (score_cache_key,),
        ).fetchone()
        return dict(row) if row else None

    def reusable_job_score(
        self,
        semantic_key: str,
        candidate_profile_hash: str,
        description_hash: str,
        prompt_version: str,
        model: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM job_scores
            WHERE semantic_key = ?
              AND candidate_profile_hash = ?
              AND description_hash = ?
              AND prompt_version = ?
              AND model = ?
              AND score_status = 'success'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (
                semantic_key,
                candidate_profile_hash,
                description_hash,
                prompt_version,
                model,
            ),
        ).fetchone()
        return dict(row) if row else None

    def failed_job_score(
        self,
        semantic_key: str,
        candidate_profile_hash: str,
        description_hash: str,
        prompt_version: str,
        model: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM job_scores
            WHERE semantic_key = ?
              AND candidate_profile_hash = ?
              AND description_hash = ?
              AND prompt_version = ?
              AND model = ?
              AND score_status = 'failed'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (
                semantic_key,
                candidate_profile_hash,
                description_hash,
                prompt_version,
                model,
            ),
        ).fetchone()
        return dict(row) if row else None

    def save_job_score(self, values: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO job_scores (
                score_cache_key, semantic_key, canonical_dedupe_key,
                candidate_profile_hash, description_hash, prompt_version,
                model, provisional, result_json, input_tokens, output_tokens,
                total_tokens, estimated_cost_usd, response_id, score_status,
                score_error, created_at
            ) VALUES (
                :score_cache_key, :semantic_key, :canonical_dedupe_key,
                :candidate_profile_hash, :description_hash, :prompt_version,
                :model, :provisional, :result_json, :input_tokens, :output_tokens,
                :total_tokens, :estimated_cost_usd, :response_id, :score_status,
                :score_error, :created_at
            )
            ON CONFLICT(score_cache_key) DO UPDATE SET
                canonical_dedupe_key = excluded.canonical_dedupe_key,
                result_json = excluded.result_json,
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                total_tokens = excluded.total_tokens,
                estimated_cost_usd = excluded.estimated_cost_usd,
                response_id = excluded.response_id,
                score_status = excluded.score_status,
                score_error = excluded.score_error,
                created_at = excluded.created_at,
                updated_at = CURRENT_TIMESTAMP
            """,
            values,
        )
        self.connection.commit()

    def latest_job_scores(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            WITH ranked AS (
                SELECT job_scores.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY semantic_key
                           ORDER BY created_at DESC,
                             score_cache_key DESC
                       ) AS score_rank
                FROM job_scores
            )
            SELECT ranked.*, jobs.title, jobs.company, jobs.location,
                   jobs.url, jobs.final_url, jobs.screening_status,
                   jobs.fetch_status, jobs.fetch_error,
                   jobs.description_confidence, jobs.description_language,
                   jobs.description, jobs.description_hash, jobs.snippet,
                   jobs.email_received_at
            FROM ranked
            JOIN jobs ON jobs.dedupe_key = ranked.canonical_dedupe_key
            WHERE ranked.score_rank = 1
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def save_application_draft(self, values: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO application_drafts (
                draft_cache_key, score_cache_key, semantic_key,
                canonical_dedupe_key, prompt_version, model, result_json,
                input_tokens, output_tokens, total_tokens, estimated_cost_usd,
                response_id, draft_status, draft_error, rewrite_attempted,
                created_at
            ) VALUES (
                :draft_cache_key, :score_cache_key, :semantic_key,
                :canonical_dedupe_key, :prompt_version, :model, :result_json,
                :input_tokens, :output_tokens, :total_tokens, :estimated_cost_usd,
                :response_id, :draft_status, :draft_error, :rewrite_attempted,
                :created_at
            )
            ON CONFLICT(draft_cache_key) DO UPDATE SET
                result_json = excluded.result_json,
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                total_tokens = excluded.total_tokens,
                estimated_cost_usd = excluded.estimated_cost_usd,
                response_id = excluded.response_id,
                draft_status = excluded.draft_status,
                draft_error = excluded.draft_error,
                rewrite_attempted = excluded.rewrite_attempted,
                created_at = excluded.created_at,
                updated_at = CURRENT_TIMESTAMP
            """,
            values,
        )
        self.connection.commit()

    def latest_application_draft(self, score_cache_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM application_drafts
            WHERE score_cache_key = ?
            ORDER BY
              CASE draft_status WHEN 'ready' THEN 0
                   WHEN 'not_applicable' THEN 1 ELSE 2 END,
              created_at DESC
            LIMIT 1
            """,
            (score_cache_key,),
        ).fetchone()
        return dict(row) if row else None

    def all_application_drafts(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM application_drafts ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def telegram_delivery(self, semantic_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM telegram_deliveries WHERE semantic_key = ?",
            (semantic_key,),
        ).fetchone()
        return dict(row) if row else None

    def save_telegram_delivery(
        self,
        semantic_key: str,
        message_id: int,
        delivered_at: str,
    ) -> None:
        score = self.connection.execute(
            """
            SELECT canonical_dedupe_key FROM job_scores
            WHERE semantic_key = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (semantic_key,),
        ).fetchone()
        canonical_dedupe_key = str(score["canonical_dedupe_key"]) if score else ""
        self.connection.execute(
            """
            INSERT INTO telegram_deliveries (
                semantic_key, canonical_dedupe_key, message_id, delivered_at,
                delivery_status, status, error
            ) VALUES (?, ?, ?, ?, 'delivered', 'delivered', '')
            ON CONFLICT(semantic_key) DO UPDATE SET
                canonical_dedupe_key = excluded.canonical_dedupe_key,
                message_id = excluded.message_id,
                delivered_at = excluded.delivered_at,
                delivery_status = 'delivered',
                status = 'delivered',
                error = '',
                applied_at = '',
                skipped_at = '',
                updated_at = CURRENT_TIMESTAMP
            """,
            (semantic_key, canonical_dedupe_key, message_id, delivered_at),
        )
        self.connection.commit()

    def update_telegram_status(
        self,
        semantic_key: str,
        status: str,
        timestamp: str,
    ) -> dict[str, Any] | None:
        delivery = self.telegram_delivery(semantic_key)
        if not delivery:
            return None
        if delivery["delivery_status"] == status:
            return delivery
        timestamp_column = "applied_at" if status == "applied" else "skipped_at"
        other_column = "skipped_at" if status == "applied" else "applied_at"
        self.connection.execute(
            f"""
            UPDATE telegram_deliveries
            SET delivery_status = ?,
                status = ?,
                {timestamp_column} = ?,
                {other_column} = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE semantic_key = ?
            """,
            (status, status, timestamp, semantic_key),
        )
        self.connection.commit()
        return self.telegram_delivery(semantic_key)

    def telegram_state(self, key: str) -> str:
        row = self.connection.execute(
            "SELECT state_value FROM telegram_state WHERE state_key = ?",
            (key,),
        ).fetchone()
        return str(row["state_value"]) if row else ""

    def set_telegram_state(self, key: str, value: str) -> None:
        self.connection.execute(
            """
            INSERT INTO telegram_state (state_key, state_value)
            VALUES (?, ?)
            ON CONFLICT(state_key) DO UPDATE SET state_value = excluded.state_value
            """,
            (key, value),
        )
        self.connection.commit()

    def start_run(self, days: int, dry_run: bool, no_telegram: bool) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO run_history (
                started_at, status, days, dry_run, no_telegram
            ) VALUES (?, 'running', ?, ?, ?)
            """,
            (_now(), days, int(dry_run), int(no_telegram)),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        counts: dict[str, int],
        estimated_cost_usd: float,
        error: str = "",
    ) -> None:
        self.connection.execute(
            """
            UPDATE run_history
            SET ended_at = ?, status = ?, counts_json = ?,
                estimated_cost_usd = ?, error = ?
            WHERE id = ?
            """,
            (
                _now(),
                status,
                json.dumps(counts, sort_keys=True),
                estimated_cost_usd,
                error,
                run_id,
            ),
        )
        self.connection.commit()

    def record_run_stage(
        self,
        run_id: int,
        stage: str,
        *,
        status: str,
        item_count: int = 0,
        error: str = "",
        started_at: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO run_stage_history (
                run_id, stage, started_at, ended_at, status, item_count, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, stage) DO UPDATE SET
                ended_at = excluded.ended_at,
                status = excluded.status,
                item_count = excluded.item_count,
                error = excluded.error
            """,
            (
                run_id,
                stage,
                started_at or _now(),
                _now(),
                status,
                item_count,
                error,
            ),
        )
        self.connection.commit()

    def record_run_item(
        self,
        run_id: int,
        item_key: str,
        stage: str,
        status: str,
        error: str = "",
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO run_item_status (
                run_id, item_key, stage, status, error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, item_key, stage) DO UPDATE SET
                status = excluded.status,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (run_id, item_key, stage, status, error, _now()),
        )
        self.connection.commit()

    def run_history(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute("SELECT * FROM run_history ORDER BY id DESC")
        ]

    def stats(self) -> list[dict[str, str | int]]:
        rows = self.connection.execute(
            "SELECT source, COUNT(*) AS count FROM jobs GROUP BY source ORDER BY source"
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self.connection.close()
