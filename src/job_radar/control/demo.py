"""Offline product walkthrough. Fake collection and Telegram, real temporary storage.

python -m job_radar.control.demo --scenario partial --walkthrough
"""

import argparse
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from job_radar.control.jobs import JobQuery
from job_radar.control.search import SearchConfig
from job_radar.control.state import ControlStore
from job_radar.control.ui import ControlCenter
from job_radar.daily import daily_cycle
from job_radar.models import Job
from job_radar.searches import SavedSearch
from job_radar.sources.base import CollectionBatch, SourceVacancy
from job_radar.storage import JobRepository


class ConsoleTelegram:
    def __init__(self):
        self.buttons = []
        self.text = ""

    def send_message(self, chat, text, markup=None):
        self.text = text
        self.buttons = [b for row in (markup or {}).get("inline_keyboard", []) for b in row]
        print("\n" + text)
        for i, button in enumerate(self.buttons, 1):
            print(f"  {i}. {button['text']}")
        return 1

    def edit_message(self, chat, message_id, text, markup=None):
        self.send_message(chat, text, markup)

    def answer_callback(self, query, text=""):
        if text:
            print(text)


class DemoSearch:
    def __init__(self, store, config, scenario):
        self.store, self.config, self.scenario = store, config, scenario
        self.started = False
        self.refreshed = False

    def profiles(self):
        return [SavedSearch("Offline fixture", "fixture", "https://example.org/jobs")]

    def start(self, selection="all", pages=1):
        self.started = True
        return {"status": "queued"}

    def status(self):
        if not self.started:
            return {"status": "idle"}
        if not self.refreshed:
            self.refreshed = True
            return {"status": "running"}
        if self.scenario in {"running", "failed"}:
            return {"status": self.scenario}
        saved = self.store.get("demo_result")
        if saved:
            return saved
        job = SourceVacancy(
            "fixture",
            "collected",
            "Junior Python — offline collection",
            "Example GmbH",
            "Vienna",
            "20 hours per week; Python APIs",
            "https://example.org/job/collected",
            "2026-11-10T12:00:00+00:00",
            "fixture",
        )
        batch = CollectionBatch(
            "success" if self.scenario == "success" else "partial",
            "https://example.org/jobs",
            (job,),
            pages_fetched=1,
            stop_reason="fixture",
        )
        report = daily_cycle(
            self.config.database,
            self.profiles(),
            lambda _: batch,
            output=self.config.output,
            enrichment_limit=0,
        )
        value = {"status": report["status"], "summary": report, "since": {"after": 1, "through": 2}}
        self.store.set("demo_result", value)
        return value


def press(center, transport, index):
    button = transport.buttons[index]
    if "url" in button:
        print("Offline mode: link stays closed.")
        return
    center.handle(
        {
            "callback_query": {
                "id": "offline",
                "data": button["callback_data"],
                "from": {"id": 42},
                "message": {"message_id": 1, "chat": {"id": 42, "type": "private"}},
            }
        }
    )


def walkthrough(center, transport):
    def click(key):
        for i, button in enumerate(transport.buttons):
            if button["text"] == center.t(key):
                press(center, transport, i)
                return
        raise RuntimeError(f"Missing walkthrough button: {key}")

    for key in ("find", "start_search", "refresh", "refresh", "recommendations"):
        click(key)
    press(center, transport, 0)
    for key in ("save", "apply", "menu", "statistics", "menu", "settings"):
        click(key)
    # Switch the same persistent preference read by real notification templates.
    index = next(i for i, b in enumerate(transport.buttons) if b["text"] == "Deutsch")
    press(center, transport, index)
    print(
        "\nOFFLINE WALKTHROUGH PASSED; real SQLite, mock Telegram and collection; no network or AI."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", choices=("running", "success", "partial", "failed"), default="success"
    )
    parser.add_argument("--walkthrough", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    with TemporaryDirectory(prefix="job-radar-offline-") as directory:
        base = Path(directory)
        database = base / "demo.db"
        with JobRepository(database) as repository:
            repository.upsert(
                [
                    Job(
                        "fixture",
                        "seed",
                        "Junior Backend — offline fixture",
                        "Demo GmbH",
                        "Vienna (Hybrid)",
                        "Python APIs; 20 hours per week",
                        "https://example.org/job/seed",
                    )
                ]
            )
            store = ControlStore(database)
            try:
                config = SearchConfig(database, base / "unused.yaml", base / "output")
                transport = ConsoleTelegram()
                center = ControlCenter(
                    JobQuery(repository, store),
                    DemoSearch(store, config, args.scenario),
                    transport,
                    42,
                    "42",
                )
                print(
                    "OFFLINE: temporary database; mock collection and Telegram; no credentials are read."
                )
                center.handle(
                    {
                        "message": {
                            "text": "/start",
                            "from": {"id": 42},
                            "chat": {"id": 42, "type": "private"},
                        }
                    }
                )
                if args.walkthrough:
                    walkthrough(center, transport)
                else:
                    while True:
                        value = input("Button number, /menu or q: ").strip()
                        if value == "q":
                            break
                        if value == "/menu":
                            center.home()
                        elif value.isdigit() and 1 <= int(value) <= len(transport.buttons):
                            press(center, transport, int(value) - 1)
            finally:
                store.close()


if __name__ == "__main__":
    main()
