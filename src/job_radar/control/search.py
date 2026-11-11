"""Durable CLI search gateway. All command arguments originate in operator config."""

import fcntl
import json
import hashlib
import math
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml

from job_radar.searches import load_searches


@dataclass(frozen=True)
class SearchConfig:
    database: Path
    searches: Path
    output: Path
    cooldown: int = 300
    timeout: int = 600
    limit: int = 15
    max_pages: int = 2
    transport: str = "playwright"
    enrichment_limit: int = 3
    score_limit: int = 2
    run_budget_usd: float = 0
    day_budget_usd: float = 0
    request_cost_usd: float = 0
    ai_ready: bool = False
    model: str = ""

    def __post_init__(self):
        if not (
            0 <= self.enrichment_limit <= 10
            and 1 <= self.score_limit <= 5
            and all(math.isfinite(n) and n >= 0 for n in (self.run_budget_usd, self.day_budget_usd, self.request_cost_usd))
            and 0 <= self.cooldown <= 86400
            and 10 <= self.timeout <= 1800
            and 1 <= self.limit <= 100
            and 1 <= self.max_pages <= 3
            and self.transport in {"http", "playwright"}
        ):
            raise ValueError("Invalid bounded search configuration")


class SearchService:
    def __init__(self, store, config: SearchConfig):
        self.store = store
        self.config = config
        self.children = []

    def profiles(self):
        try:
            return load_searches(self.config.searches)
        except (ValueError, OSError, yaml.YAMLError):
            return []

    def _lock_path(self, name):
        return self.config.database.with_suffix(self.config.database.suffix + name)

    def locked(self, name):
        path = self._lock_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
        return False

    def status(self):
        state = self.store.get("search", {"status": "idle"})
        if state["status"] in {"queued", "running"}:
            if not self.locked(".telegram-search.lock") and not self.locked(".run.lock"):
                state.update(status="interrupted", ended=time.time())
                self.store.set("search", state)
                self.store.completed(state)
        return state

    def quote(self):
        c = self.config
        allowed = (c.ai_ready and c.request_cost_usd > 0 and c.run_budget_usd >= c.request_cost_usd
                   and c.day_budget_usd >= c.request_cost_usd)
        quote = {"available": bool(allowed), "run_cap": c.run_budget_usd, "day_cap": c.day_budget_usd,
                 "maximum": min(c.score_limit * c.request_cost_usd, c.run_budget_usd), "model": c.model}
        quote["fingerprint"] = hashlib.sha256(json.dumps(quote, sort_keys=True).encode()).hexdigest()
        return quote

    def start(self, selection="all", pages=1, *, mode="quick", confirmation=None):
        if mode not in {"quick", "full"}:
            raise ValueError("Invalid search mode")
        quote = self.quote()
        if mode == "full" and (not quote["available"] or confirmation != quote["fingerprint"]):
            return {"status": "ai_unavailable"}
        # Reap detached supervisors without making their lifetime depend on polling.
        self.children = [child for child in self.children if child.poll() is None]
        handle = self._lock_path(".telegram-search.lock").open("a+")
        try:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {**self.status(), "status": "running"}
            if self.locked(".run.lock"):
                return {"status": "running"}
            old = self.store.get("search", {})
            remaining = self.config.cooldown - (time.time() - old.get("started", 0))
            if remaining > 0:
                return {"status": "cooldown", "seconds": math.ceil(remaining)}
            profiles = self.profiles()
            # Distinguish profile and source namespaces even when their names collide.
            if selection.startswith("p:"):
                profiles = [p for p in profiles if p.name == selection[2:]]
            elif selection.startswith("s:"):
                profiles = [p for p in profiles if p.source == selection[2:]]
            elif selection != "all":
                profiles = []
            if not profiles:
                return {"status": "config_missing"}
            if not isinstance(pages, int) or not 1 <= pages <= self.config.max_pages:
                raise ValueError("Invalid depth")
            run = uuid.uuid4().hex
            output = self.config.output.resolve() / run
            output.mkdir(parents=True, mode=0o700)
            search_file = output / "searches.yaml"
            search_file.write_text(
                yaml.safe_dump({"searches": [p.to_config() for p in profiles]})
            )
            state = {
                "id": run,
                "status": "queued",
                "started": time.time(),
                "output": str(output),
                "baseline": self._max_run(),
                "before_jobs": self._max_jobs(),
                "mode": mode,
            }
            self.store.set("search", state)
            command = [
                sys.executable,
                "-m",
                "job_radar.cli",
                "daily",
                "--database",
                str(self.config.database.resolve()),
                "--searches",
                str(search_file),
                "--output",
                str(output),
                "--transport",
                self.config.transport,
                "--pages",
                str(pages),
                "--limit",
                str(self.config.limit),
                "--details",
                "0",
                "--enrich-limit",
                str(self.config.enrichment_limit),
                "--score-limit",
                str(self.config.score_limit if mode == "full" else 0),
                "--no-email",
            ]
            if mode == "full":
                command += ["--ai-run-budget-usd", str(self.config.run_budget_usd),
                            "--ai-day-budget-usd", str(self.config.day_budget_usd)]
            try:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "job_radar.control.worker",
                        str(self.config.database.resolve()),
                        str(handle.fileno()),
                        str(self.config.timeout),
                        run,
                        json.dumps(command),
                    ],
                    pass_fds=(handle.fileno(),),
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.children.append(child)
            except OSError:
                state.update(status="failed", ended=time.time())
                self.store.set("search", state)
            return state
        finally:
            # close, not LOCK_UN: the supervisor inherits ownership of this flock.
            handle.close()

    def shutdown(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
        self.children.clear()

    def _max_run(self):
        import sqlite3

        with sqlite3.connect(self.config.database) as connection:
            return connection.execute("SELECT COALESCE(MAX(id),0) FROM run_history").fetchone()[0]

    def _max_jobs(self):
        import sqlite3

        with sqlite3.connect(self.config.database) as connection:
            return connection.execute("SELECT COALESCE(MAX(rowid),0) FROM jobs").fetchone()[0]
