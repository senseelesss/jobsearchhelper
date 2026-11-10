"""Bounded read adapter and disk-backed stable browse snapshots.

The derived sidecar index is disposable. JobRepository remains authoritative for
application state; collection, scoring, and eligibility algorithms are reused.
"""

import json
import hashlib
import re
import time
from datetime import datetime, timezone

from job_radar.availability import is_expired
from job_radar.candidate import candidate_profile_hash
from job_radar.eligibility import assess_application, load_policy
from job_radar.matching import SCORE_PROMPT_VERSION, _effective_vacancy, has_full_description

VIEWS = {"new", "recommendations", "saved", "applied", "skipped", "all", "applications"}
SORTS = {"newest", "priority_sort", "score_sort"}
FILTERS = {"priority", "source", "work_mode", "hours", "state"}


class JobQuery:
    def __init__(self, repository, store, profile=None, model=""):
        self.repository = repository
        self.store = store
        self.profile = profile
        self.model = model
        self.last_sync = 0.0
        self._statistics = None
        self._statistics_at = 0.0

    def enrich(self, row):
        row = dict(row)
        application = self.repository.application_for(row["dedupe_key"])
        row.update(
            application_state=application["state"],
            application_revision=application["revision"],
            application_key=application.get("dedupe_key", row["dedupe_key"]),
            application_date=application.get("updated_at"),
        )
        applied = self.repository.connection.execute(
            "SELECT created_at FROM application_events WHERE dedupe_key=? AND state='applied' "
            "ORDER BY created_at DESC LIMIT 1",
            (row["application_key"],),
        ).fetchone()
        row["applied_at"] = applied[0] if applied else None
        score = None
        if self.profile is not None and self.model:
            identity = (
                row["semantic_key"],
                candidate_profile_hash(self.profile),
                _effective_vacancy(row)["description_hash"],
                SCORE_PROMPT_VERSION,
                self.model,
            )
            score = self.repository.reusable_job_score(
                *identity
            ) or self.repository.failed_job_score(*identity)
        historical = self.repository.connection.execute(
            "SELECT score_status,result_json,estimated_cost_usd,score_cache_key FROM job_scores WHERE semantic_key=? "
            "ORDER BY created_at DESC,score_cache_key DESC LIMIT 1",
            (row["semantic_key"],),
        ).fetchone()
        if historical and not score:
            row["historical_score"] = json.loads(historical["result_json"]).get("match_score")
            row["historical_status"] = historical["score_status"]
            row["historical_cost"] = historical["estimated_cost_usd"]
            row["historical_cache_key"] = historical["score_cache_key"]
        row["score_status"] = "not_started" if self.profile is not None else "context_unavailable"
        if score:
            result = json.loads(score["result_json"])
            for key in (
                "match_score",
                "recommendation",
                "matching_strengths",
                "missing_requirements",
                "provisional",
            ):
                row[key] = result.get(key)
            row.update(
                score_status=score["score_status"],
                score_cost=score["estimated_cost_usd"],
                score_cache_key=score["score_cache_key"],
            )
        row.update(assess_application(row, self.profile))
        row["full_jd"] = has_full_description(row)
        row["active"] = not is_expired(row)
        row["recommended"] = (
            row["active"]
            and row["application_eligibility"] != "ineligible"
            and row["application_state"] in {"not_applied", "saved"}
            and (
                row["score_status"] == "not_started"
                and not historical
                or (
                    row["score_status"] == "success"
                    and (
                        row.get("recommendation") in {"apply", "strong_apply"}
                        or row.get("recommendation") == "review"
                        and (row.get("match_score") or 0) >= 50
                    )
                )
                or row["score_status"] == "context_unavailable"
                and not historical
            )
        )
        location = row["location"].lower()
        row["work_mode"] = (
            "hybrid"
            if "hybrid" in location
            else "remote"
            if "remote" in location
            else "onsite"
            if re.search(r"on[ -]?site|vor ort", location)
            else "unknown"
        )
        return row

    def detail(self, key):
        row = self.repository.connection.execute(
            "SELECT rowid AS _rowid,* FROM jobs WHERE dedupe_key=?", (key,)
        ).fetchone()
        return self.enrich(row) if row else None

    def legacy_identity_matches(self, row, chat_id):
        chat_hash = hashlib.sha256(str(chat_id).encode()).hexdigest()[:16]
        marker = self.repository.connection.execute(
            "SELECT semantic_key FROM daily_alert_items WHERE chat_hash=? AND dedupe_key=?",
            (chat_hash, row["dedupe_key"]),
        ).fetchone()
        return marker is not None and marker[0] == row["semantic_key"]

    def sync(self, force=False):
        if not force and time.monotonic() - self.last_sync < 15:
            return
        connection = self.store.connection
        # Stamp only metadata for unchanged rows. Fetch descriptions in bounded batches.
        cursor = self.repository.connection.execute("""
            SELECT j.dedupe_key, j.updated_at, j.semantic_key, j.source, j.title,
            j.company, j.location, j.snippet, j.description_hash, j.fetch_status,
            j.fetched_at, j.fetch_error, j.description_confidence, j.screening_status,
            j.published_at, j.created_at,
            (SELECT MAX(created_at) FROM job_scores s WHERE s.semantic_key=j.semantic_key) AS score_created,
            (SELECT MAX(updated_at) FROM job_scores s WHERE s.semantic_key=j.semantic_key) AS score_stamp,
            (SELECT MAX(updated_at) FROM applications a WHERE a.semantic_key=j.semantic_key) AS app_stamp,
            (SELECT MAX(updated_at) FROM telegram_deliveries d WHERE d.semantic_key=j.semantic_key) AS delivery_stamp
            FROM jobs j ORDER BY j.dedupe_key
        """)
        context = json.dumps([self.profile, self.model, load_policy()], sort_keys=True)
        marker = (
            "index-v1:"
            + datetime.now(timezone.utc).date().isoformat()
            + hashlib.sha256(context.encode()).hexdigest()
        )
        connection.execute("CREATE TEMP TABLE IF NOT EXISTS seen(key TEXT PRIMARY KEY)")
        connection.execute("DELETE FROM seen")
        while batch := cursor.fetchmany(100):
            for item in batch:
                key = item["dedupe_key"]
                connection.execute("INSERT INTO seen VALUES (?)", (key,))
                stamp = json.dumps(list(item)[1:]) + marker
                cached = connection.execute(
                    "SELECT stamp FROM job_index WHERE key=?", (key,)
                ).fetchone()
                if cached and cached[0] == stamp:
                    continue
                row = self.detail(key)
                if row is None:
                    continue
                connection.execute(
                    """INSERT OR REPLACE INTO job_index VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        key,
                        row["semantic_key"],
                        stamp,
                        row["source"],
                        row["application_state"],
                        row["application_priority"],
                        row.get("match_score"),
                        int(row["recommended"]),
                        int(row["active"]),
                        int(row["full_jd"]),
                        row["created_at"],
                        row["work_mode"],
                        row["hours_fit"],
                        json.dumps(row, ensure_ascii=False),
                    ),
                )
            connection.commit()
        with connection:
            connection.execute("DELETE FROM job_index WHERE key NOT IN (SELECT key FROM seen)")
        self.last_sync = time.monotonic()

    @staticmethod
    def canonical():
        return """WITH ranked AS (SELECT *, ROW_NUMBER() OVER (
            PARTITION BY semantic ORDER BY active DESC, full_jd DESC, created DESC, key
        ) AS rank FROM job_index), canonical AS (SELECT * FROM ranked WHERE rank=1) """

    def snapshot(self, view="all", sort="newest", filters=None, since=None):
        if view not in VIEWS or sort not in SORTS or set(filters or {}) - FILTERS:
            raise ValueError("Invalid browse options")
        self.sync()
        where, parameters = ["1=1"], []
        if view == "new":
            where.append("state='not_applied'")
        elif view == "recommendations":
            where.append("recommended=1")
        elif view == "applications":
            where.append("state IN ('applied','interview','offer','rejected','withdrawn')")
        elif view in {"saved", "applied", "skipped"}:
            where.append("state=?")
            parameters.append(view)
        for field, value in (filters or {}).items():
            where.append(f"{field}=?")  # field is validated against FILTERS above
            parameters.append(value)
        if isinstance(since, dict):
            where.append(
                "json_extract(payload,'$._rowid')>? AND json_extract(payload,'$._rowid')<=?"
            )
            parameters.extend((int(since["after"]), int(since["through"])))
        elif since:
            where.append("datetime(created)>=datetime(?)")
            parameters.append(since)
        order = {
            "newest": "created DESC, key",
            "priority_sort": "CASE priority WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 WHEN 'LOW' THEN 2 ELSE 3 END, created DESC, key",
            "score_sort": "score DESC NULLS LAST, created DESC, key",
        }[sort]
        data = self.store.token(
            {"action": "page", "view": view, "sort": sort, "filters": filters or {}, "since": since}
        )
        query = self.canonical() + "SELECT * FROM canonical WHERE " + " AND ".join(where)
        with self.store.connection:
            self.store.connection.execute(
                "INSERT INTO snapshots(token,position,key) SELECT ?, ROW_NUMBER() OVER (ORDER BY "
                + order
                + "),key FROM ("
                + query
                + ")",
                (data[3:], *parameters),
            )
        return data[3:]

    def page(self, token, page=0, size=5):
        if page < 0 or page > 100000:
            raise ValueError("Invalid page")
        rows = self.store.connection.execute(
            "SELECT key FROM snapshots WHERE token=? AND position>? ORDER BY position LIMIT ?",
            (token, page * size, size + 1),
        ).fetchall()
        return [dict(row) for row in rows[:size]], len(rows) > size

    def position(self, token, position):
        row = self.store.connection.execute(
            "SELECT key FROM snapshots WHERE token=? AND position=?", (token, position)
        ).fetchone()
        return row[0] if row else None

    def options(self, field):
        if field not in FILTERS:
            raise ValueError("Invalid filter")
        self.sync()
        return [
            row[0]
            for row in self.store.connection.execute(
                f"SELECT DISTINCT {field} FROM job_index ORDER BY {field} LIMIT 30"
            )
        ]

    def statistics(self):
        if (
            self._statistics is not None
            and self.last_sync
            and time.monotonic() - self._statistics_at < 15
        ):
            return self._statistics
        self.sync()
        c = self.store.connection
        base = self.canonical()
        result = dict(
            c.execute(
                base
                + """SELECT COUNT(*) AS total,
            SUM(datetime(created)>=datetime('now','-1 day')) AS today,
            SUM(datetime(created)>=datetime('now','-7 days')) AS week,
            SUM(state='saved') AS saved,
            SUM(state IN ('applied','interview','offer','rejected','withdrawn')) AS applications,
            SUM(state='skipped') AS skipped, SUM(full_jd) AS full,
            SUM(score IS NOT NULL) AS score_count FROM canonical"""
            ).fetchone()
        )
        result = {key: value if value is not None else 0 for key, value in result.items()}
        result["unreviewed"] = c.execute(
            base
            + """SELECT COUNT(*) FROM canonical j
            WHERE recommended=1 AND NOT EXISTS (
                SELECT 1 FROM preferences p WHERE p.key='reviewed:' || j.semantic)"""
        ).fetchone()[0]
        for field in ("source", "priority", "state"):
            result[field] = [
                (r[0], r[1])
                for r in c.execute(
                    base + f"SELECT {field},COUNT(*) FROM canonical GROUP BY {field}"
                )
            ]
        self._statistics, self._statistics_at = result, time.monotonic()
        return result
