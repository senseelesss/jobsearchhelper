"""Private sidecar state; no migrations to the collection database."""

import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any


class ControlStore:
    def __init__(self, database: Path):
        self.path = database.resolve().with_suffix(database.suffix + ".telegram.sqlite3")
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS callbacks(
                token TEXT PRIMARY KEY, payload TEXT NOT NULL, expires REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS callback_expiry ON callbacks(expires);
            CREATE TABLE IF NOT EXISTS job_index(
                key TEXT PRIMARY KEY, semantic TEXT NOT NULL, stamp TEXT NOT NULL,
                source TEXT NOT NULL, state TEXT NOT NULL, priority TEXT NOT NULL,
                score REAL, recommended INTEGER NOT NULL, active INTEGER NOT NULL,
                full_jd INTEGER NOT NULL, created TEXT NOT NULL, work_mode TEXT NOT NULL,
                hours TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS snapshots(
                token TEXT NOT NULL, position INTEGER NOT NULL, key TEXT NOT NULL,
                PRIMARY KEY(token, position));
        """)

    def close(self):
        self.connection.close()

    def get(self, key: str, default: Any = None) -> Any:
        row = self.connection.execute(
            "SELECT value FROM preferences WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value: Any):
        with self.connection:
            self.connection.execute(
                "INSERT INTO preferences VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def token(self, payload: dict, ttl: int = 86400) -> str:
        token = secrets.token_urlsafe(12)
        with self.connection:
            self.connection.execute(
                "INSERT INTO callbacks VALUES (?,?,?)",
                (token, json.dumps(payload), time.time() + ttl),
            )
        return "cc:" + token

    def resolve(self, data: str) -> dict | None:
        if not data.startswith("cc:") or len(data) > 64:
            return None
        row = self.connection.execute(
            "SELECT payload FROM callbacks WHERE token=? AND expires>?", (data[3:], time.time())
        ).fetchone()
        return json.loads(row[0]) if row else None

    def prune(self):
        with self.connection:
            self.connection.execute(
                "DELETE FROM snapshots WHERE token IN (SELECT token FROM callbacks WHERE expires<=?)",
                (time.time(),),
            )
            self.connection.execute("DELETE FROM callbacks WHERE expires<=?", (time.time(),))
            self.connection.execute(
                "DELETE FROM preferences WHERE key LIKE 'request:cc:%' AND substr(key,12) "
                "NOT IN (SELECT token FROM callbacks)"
            )
