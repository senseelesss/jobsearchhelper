from pathlib import Path
import subprocess
import sys
import tomllib

from job_radar import __version__
from job_radar.cli import _application_set, _daily, parser
from job_radar.config import Settings
from job_radar.searches import load_searches
from job_radar.sources.base import CollectionBatch
from job_radar.storage import JobRepository

ROOT = Path(__file__).resolve().parents[1]


def test_release_version_has_one_authoritative_definition():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert __version__ == "1.1.0"
    assert "version" not in project["project"]
    assert project["tool"]["setuptools"]["dynamic"]["version"]["attr"] == "job_radar.__version__"
    result = subprocess.run(
        [sys.executable, "-m", "job_radar.cli", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == f"job-radar {__version__}"


def test_fresh_config_daily_and_callbacks_share_database(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/candidate_profile.yaml").write_text(
        (ROOT / "candidate_profile.example.yaml").read_text()
    )
    (tmp_path / "data/searches.yaml").write_text(
        (ROOT / "deploy/searches.vienna.example.yaml").read_text()
    )
    (tmp_path / ".env").write_text("JOB_RADAR_DB=private-state/vacancies.db\n")
    monkeypatch.delenv("JOB_RADAR_DB", raising=False)
    settings = Settings.from_env()
    assert settings.database_path == Path("private-state/vacancies.db")
    assert len(load_searches(Path("data/searches.yaml"))) == 6
    monkeypatch.setattr(
        "job_radar.collection.collect_search",
        lambda client, url, **kwargs: CollectionBatch("empty", url),
    )
    args = parser().parse_args(["daily", "--no-email", "--enrich-limit", "0", "--output", "out"])
    _daily(args, settings)
    assert settings.database_path.is_file()
    assert not Path("data/mvp-v1-test.db").exists()
    with JobRepository(settings.database_path) as repo:
        assert repo.connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert repo.run_history()[0]["status"] == "success"


def test_explicit_database_overrides_config_and_application_set_uses_same_default(
    tmp_path, monkeypatch
):
    from job_radar.models import Job

    monkeypatch.chdir(tmp_path)
    settings = Settings(Path("custom/jobs.db"), Path("client.json"), Path("token.json"))
    with JobRepository(settings.database_path) as repo:
        repo.upsert(
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
    args = parser().parse_args(
        ["application-set", "linkedin:123", "skipped", "--request-id", "release-test"]
    )
    _application_set(args, settings)
    with JobRepository(settings.database_path) as repo:
        assert repo.application_for("linkedin:123")["state"] == "skipped"
    parsed = parser().parse_args(["daily", "--database", "isolated/jobs.db"])
    assert parsed.database == "isolated/jobs.db"
