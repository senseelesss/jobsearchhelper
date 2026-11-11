import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def backup_database(source: Path, directory: Path, keep: int | None = 7) -> Path:
    if not source.is_file():
        raise ValueError("Database does not exist")
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"job-radar-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}.db"
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as original:
        with sqlite3.connect(output) as target:
            original.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
    if keep is not None:
        for old in sorted(directory.glob("job-radar-*.db"))[: -max(1, keep)]:
            old.unlink()
    return output
