"""Detached bounded supervisor, deliberately independent of Telegram/network SDKs."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from job_radar.control.state import ControlStore


def supervise(database: Path, lock_fd: int, timeout: int, run: str, command: list[str]):
    store = ControlStore(database)
    state = store.get("search", {})
    child = None

    def stop(*_):
        raise InterruptedError("supervisor stopped")

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        if state.get("id") != run:
            return
        state["status"] = "running"
        store.set("search", state)
        # No raw collector output is retained or sent to Telegram.
        child = subprocess.Popen(
            command, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        code = child.wait(timeout=timeout)
        report_path = Path(state["output"]) / "report.json"
        report = json.loads(report_path.read_text()) if report_path.is_file() else {}
        with sqlite3.connect(database, timeout=5) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM run_history WHERE id=? AND id>?",
                (report.get("run_id", -1), state["baseline"]),
            ).fetchone()
            through = connection.execute("SELECT COALESCE(MAX(rowid),0) FROM jobs").fetchone()[0]
        if row and row["status"] in {"success", "partial"}:
            report = json.loads(row["counts_json"])
            state.update(
                status=row["status"],
                run_id=row["id"],
                summary={
                    key: report[key]
                    for key in ("inserted", "shortlisted", "sources", "enrichment")
                    if key in report
                },
                since={"after": state["before_jobs"], "through": through},
            )
        else:
            state["status"] = "failed" if code else "interrupted"
    except subprocess.TimeoutExpired:
        state["status"] = "timeout"
    except InterruptedError:
        state["status"] = "interrupted"
    except Exception:
        state["status"] = "failed"
    finally:
        if child is not None:
            # Terminate browsers/other descendants as well as the CLI process.
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        state["ended"] = time.time()
        if store.get("search", {}).get("id") == run:
            store.set("search", state)
        signal.signal(signal.SIGTERM, previous)
        os.close(lock_fd)
        store.close()


if __name__ == "__main__":
    os.umask(0o077)
    supervise(
        Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], json.loads(sys.argv[5])
    )
