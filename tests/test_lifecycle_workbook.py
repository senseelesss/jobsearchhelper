from datetime import datetime, timezone, timedelta

from openpyxl import load_workbook

from job_radar.availability import group_expired
from job_radar.enrichment import enrich_jobs
from job_radar.matching import scoring_candidates
from job_radar.models import Job
from job_radar.runner import _active_semantic_keys
from job_radar.storage import JobRepository
from job_radar.workbook import export_workbook, import_status_edits
from job_radar.http import HttpResponse


def job(source="linkedin", identifier="1"):
    return Job(
        source,
        identifier,
        "Junior Python Developer",
        "Example GmbH",
        "Vienna, Austria",
        "Python APIs",
        f"https://www.linkedin.com/jobs/view/{identifier}/",
    )


def test_expired_duplicate_cannot_hide_active_source(tmp_path):
    with JobRepository(tmp_path / "jobs.db") as repo:
        repo.upsert([job(), job("indeed", "2")])
        repo.screen_all()
        repo.connection.execute("UPDATE jobs SET fetch_status='expired' WHERE source='linkedin'")
        repo.connection.execute(
            "UPDATE jobs SET fetch_status='success', description='Full Python requirements' WHERE source='indeed'"
        )
        repo.connection.commit()
        assert len(_active_semantic_keys(repo)) == 1
        assert scoring_candidates(repo, None)[0]["source"] == "indeed"
        assert not group_expired(repo.all())


def test_successful_description_is_refetched_when_stale_and_retained_on_transient_error(tmp_path):
    class Client:
        calls = 0
        status = 200

        def get(self, url):
            self.calls += 1
            return HttpResponse(
                self.status,
                '<div class="show-more-less-html__markup">Updated Python API requirements.</div>',
                url,
            )

    client = Client()
    with JobRepository(tmp_path / "jobs.db") as repo:
        repo.upsert([job()])
        repo.screen_all()
        enrich_jobs(repo, client)
        assert client.calls == 1
        enrich_jobs(repo, client)
        assert client.calls == 1
        old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        repo.connection.execute("UPDATE jobs SET fetched_at=?", (old,))
        repo.connection.execute("UPDATE enrichment_cache SET fetched_at=?", (old,))
        repo.connection.commit()
        client.status = 503
        enrich_jobs(repo, client, retry_failed=True)
        assert client.calls == 2
        row = repo.all()[0]
        assert row["description"] == "Updated Python API requirements."
        assert row["fetched_at"] == old
        assert row["fetch_error"].startswith("Refresh failed")


def test_full_requirements_are_used_for_screening(tmp_path):
    with JobRepository(tmp_path / "jobs.db") as repo:
        repo.upsert([job()])
        repo.connection.execute(
            "UPDATE jobs SET description='Minimum 8 years of professional experience'"
        )
        repo.connection.commit()
        repo.screen_all()
        assert repo.all()[0]["screening_status"] == "rejected"


def test_excel_import_replay_and_newer_telegram_revision_conflict(tmp_path):
    path = tmp_path / "jobs.xlsx"
    export_workbook(
        [{"dedupe_key": "linkedin:1", "title": '=HYPERLINK("evil")', "application_revision": 0}],
        path,
    )
    workbook = load_workbook(path)
    assert workbook["Вакансії"]["C6"].data_type == "s"
    edits = workbook["Зміни статусів"]
    for col, value in enumerate(["linkedin:1", 0, "applied", "User confirmed submission"], 1):
        edits.cell(6, col, value)
    workbook.save(path)
    workbook.close()
    with JobRepository(tmp_path / "jobs.db") as repo:
        repo.upsert([job()])
        first = import_status_edits(repo, path, tmp_path / "receipt.json")
        assert first[0]["result"] == "updated"
        assert import_status_edits(repo, path, tmp_path / "receipt.json")[0]["result"] == "replay"
        workbook = load_workbook(path)
        workbook["Зміни статусів"]["C6"] = "skipped"
        workbook.save(path)
        workbook.close()
        conflict = import_status_edits(repo, path, tmp_path / "receipt.json")
        assert conflict[0]["result"] == "rejected"
        assert repo.application_for("linkedin:1")["state"] == "applied"


def test_canonical_source_change_keeps_excel_edit_identity_and_revision(tmp_path):
    from job_radar.daily import review_rows

    with JobRepository(tmp_path / "jobs.db") as repository:
        repository.upsert([job(), job("karriere", "2")])
        repository.set_application("linkedin:1", "applied", "first")
        rows = review_rows(repository)
        assert len(rows) == 1
        assert rows[0]["application_key"] == "linkedin:1"
        assert rows[0]["application_revision"] == 1
        repository.set_application(
            rows[0]["application_key"], "saved", "second", expected_revision=1
        )
        assert repository.application_for("karriere:2")["state"] == "saved"
