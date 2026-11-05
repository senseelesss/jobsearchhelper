from pathlib import Path
from types import SimpleNamespace

from job_radar.cli import _telegram_connect
from job_radar.models import Job
from job_radar.storage import JobRepository
from job_radar.telegram import process_callback


def test_connection_check_never_sends_or_prints_credentials(monkeypatch, tmp_path, capsys):
    calls = []

    class Client:
        def __init__(self, token):
            pass

        def _call(self, method, payload, timeout):
            calls.append(method)
            return {"is_bot": True} if method == "getMe" else {"id": 123}

        def send_message(self, *args):
            raise AssertionError("A check must not send")

    monkeypatch.setattr("job_radar.telegram.TelegramApiClient", Client)
    monkeypatch.chdir(tmp_path)
    settings = SimpleNamespace(telegram_bot_token="secret-token", telegram_chat_id="private-chat")
    _telegram_connect(SimpleNamespace(command="telegram-check", test_message=False), settings)
    assert calls == ["getMe", "getChat"]
    assert not Path(".env").exists()
    output = capsys.readouterr().out
    assert "secret-token" not in output and "private-chat" not in output
    _telegram_connect(SimpleNamespace(command="telegram-connect", test_message=False), settings)
    assert Path(".env").stat().st_mode & 0o777 == 0o600


def test_daily_buttons_record_independent_application_events_and_ignore_other_chat(tmp_path):
    class Transport:
        answers = []

        def answer_callback(self, *args):
            self.answers.append(args)

    transport = Transport()
    with JobRepository(tmp_path / "jobs.db") as repository:
        repository.upsert(
            [
                Job(
                    "linkedin",
                    "123",
                    "Junior Python Developer",
                    "Example",
                    "Vienna",
                    "",
                    "https://www.linkedin.com/jobs/view/123/",
                )
            ]
        )
        callback = {
            "id": "event1",
            "data": "applyjob:linkedin:123",
            "message": {"chat": {"id": "allowed"}},
        }
        process_callback(repository, transport, "different", callback)
        assert repository.application_for("linkedin:123")["state"] == "not_applied"
        process_callback(repository, transport, "allowed", callback)
        process_callback(repository, transport, "allowed", callback)
        assert repository.application_for("linkedin:123")["state"] == "applied"
        assert (
            repository.connection.execute("SELECT COUNT(*) FROM application_events").fetchone()[0]
            == 1
        )
