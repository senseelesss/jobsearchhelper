import sqlite3

from job_radar.storage import JobRepository


def test_sqlite_reliability_pragmas_are_enabled(tmp_path) -> None:
    with JobRepository(tmp_path / "jobs.db") as repository:
        journal_mode = repository.connection.execute(
            "PRAGMA journal_mode"
        ).fetchone()[0]
        busy_timeout = repository.connection.execute(
            "PRAGMA busy_timeout"
        ).fetchone()[0]
        foreign_keys = repository.connection.execute(
            "PRAGMA foreign_keys"
        ).fetchone()[0]

    assert journal_mode == "wal"
    assert busy_timeout == 5000
    assert foreign_keys == 1


def test_current_head_telegram_schema_migrates_additively(tmp_path) -> None:
    path = tmp_path / "jobs.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE telegram_deliveries (
            semantic_key TEXT PRIMARY KEY,
            canonical_dedupe_key TEXT NOT NULL,
            message_id TEXT NOT NULL,
            delivered_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'delivered',
            error TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        """
        INSERT INTO telegram_deliveries (
            semantic_key, canonical_dedupe_key, message_id, delivered_at,
            status, error
        ) VALUES ('semantic-old', 'linkedin:old', '42', '2026-06-23', 'delivered', '')
        """
    )
    connection.commit()
    connection.close()

    with JobRepository(path) as repository:
        columns = {
            row["name"]
            for row in repository.connection.execute(
                "PRAGMA table_info(telegram_deliveries)"
            )
        }
        delivery = repository.telegram_delivery("semantic-old")

    assert {"delivery_status", "applied_at", "skipped_at"} <= columns
    assert delivery["delivery_status"] == "delivered"
    assert delivery["message_id"] == "42"
