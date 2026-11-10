import json
import sqlite3

import pytest

from job_radar.models import Job
from job_radar.screening import screen_job
from job_radar.storage import JobRepository


def job(
    source: str,
    source_job_id: str,
    title: str,
    company: str = "Example GmbH",
    location: str = "Vienna, Austria",
) -> Job:
    return Job(
        source=source,
        source_job_id=source_job_id,
        title=title,
        company=company,
        location=location,
        snippet="",
        url=f"https://example.com/jobs/{source_job_id}",
        email_message_id=f"email-{source_job_id}",
        email_received_at="2026-06-23T08:00:00Z",
        alert_subject="Jobs",
    )


@pytest.mark.parametrize(
    "title",
    [
        "Senior Backend Engineer",
        "Principal Software Engineer",
        "Technical Lead",
        "Head of Appraisals",
        "Engineering Director",
    ],
)
def test_senior_roles_are_rejected(title: str) -> None:
    result = screen_job(title, "Vienna, Austria")

    assert result.status == "rejected"
    assert any(reason["code"] == "seniority" for reason in result.rejection_reasons)


@pytest.mark.parametrize(
    "title",
    [
        "Sales Account Executive",
        "Accounting Specialist",
        "Treasury Analyst",
        "HR Generalist",
        "Marketing Manager",
    ],
)
def test_non_technical_functions_are_reviewed(title: str) -> None:
    result = screen_job(title, "Vienna, Austria")

    assert result.status == "review"
    assert any(
        reason["code"] == "adjacent_role"
        for reason in result.rejection_reasons
    )


def test_outside_vienna_is_rejected_unless_remote_austria() -> None:
    outside = screen_job("Junior Backend Engineer", "Graz, Austria")
    remote = screen_job("Junior Backend Engineer", "Remote, Austria")

    assert outside.status == "rejected"
    assert any(
        reason["code"] == "location_outside_target"
        for reason in outside.rejection_reasons
    )
    assert remote.status == "eligible"


def test_junior_app_manager_is_not_rejected_for_manager() -> None:
    result = screen_job("Junior App Manager", "Wien, Österreich")

    assert result.status == "eligible"


def test_application_manager_is_not_rejected() -> None:
    result = screen_job("Application Manager", "Wien, Österreich")

    assert result.status == "eligible"


def test_working_student_communications_requires_review() -> None:
    result = screen_job(
        "Werkstudent:in Communications",
        "Wien, Österreich",
        "Support communications for the IT department.",
    )

    assert result.status == "review"
    assert any(
        reason["code"] == "adjacent_role"
        for reason in result.rejection_reasons
    )


@pytest.mark.parametrize(
    "title",
    [
        "Junior Backend Engineer",
        "Working Student Software Engineering",
        "Werkstudent Python",
        "Software Engineering Internship",
        "Entry-Level API Developer",
        "Part-Time Automation Developer",
    ],
)
def test_priority_roles_are_eligible(title: str) -> None:
    assert screen_job(title, "Vienna, Austria").status == "eligible"


def test_relevant_technical_role_without_junior_keyword_is_eligible() -> None:
    result = screen_job("Backend Engineer", "Vienna, Austria")

    assert result.status == "eligible"


def test_five_year_requirement_is_rejected() -> None:
    result = screen_job(
        "Backend Engineer",
        "Vienna, Austria",
        "Minimum 5 years of professional Python experience required.",
    )

    assert result.status == "rejected"
    assert any(
        reason["code"] == "excessive_experience"
        for reason in result.rejection_reasons
    )


def test_expired_vacancy_is_rejected() -> None:
    result = screen_job(
        "Junior Backend Engineer",
        "Vienna, Austria",
        fetch_status="expired",
    )

    assert result.status == "rejected"
    assert any(
        reason["code"] == "expired_vacancy"
        for reason in result.rejection_reasons
    )


def test_clearly_irrelevant_profession_is_rejected() -> None:
    result = screen_job("Warehouse Worker", "Vienna, Austria")

    assert result.status == "rejected"
    assert any(
        reason["code"] == "irrelevant_profession"
        for reason in result.rejection_reasons
    )


def test_semantic_duplicates_remain_separate_source_records(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    try:
        linked_in = job("linkedin", "linkedin-1", "Junior Backend Engineer")
        indeed = job("indeed", "indeed-1", "  JUNIOR backend engineer ")

        inserted, updated = repository.upsert([linked_in, indeed])
        rows = repository.all()
    finally:
        repository.close()

    assert (inserted, updated) == (2, 0)
    assert len(rows) == 2
    assert rows[0]["dedupe_key"] != rows[1]["dedupe_key"]
    assert rows[0]["semantic_key"] == rows[1]["semantic_key"]


def test_screening_persists_structured_reasons_and_rules_version(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    try:
        repository.upsert(
            [
                job(
                    "indeed",
                    "senior-1",
                    "Senior Backend Engineer",
                    location="Graz, Austria",
                )
            ]
        )
        counts = repository.screen_all()
        row = repository.all()[0]
    finally:
        repository.close()

    assert counts == {"eligible": 0, "review": 0, "rejected": 1}
    assert row["screening_status"] == "rejected"
    assert row["screening_rules_version"] == "screening-v4-eligibility"
    assert isinstance(row["rejection_reasons"], list)
    assert {reason["code"] for reason in row["rejection_reasons"]} == {
        "seniority",
        "location_outside_target",
    }


def test_existing_database_is_migrated_and_screened(tmp_path) -> None:
    database_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE jobs (
            dedupe_key TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_job_id TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT NOT NULL,
            snippet TEXT NOT NULL,
            url TEXT NOT NULL,
            email_message_id TEXT NOT NULL,
            email_received_at TEXT NOT NULL,
            alert_subject TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        INSERT INTO jobs (
            dedupe_key, source, source_job_id, title, company, location, snippet,
            url, email_message_id, email_received_at, alert_subject
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "indeed:legacy-1",
            "indeed",
            "legacy-1",
            "Junior Backend Engineer",
            "Example GmbH",
            "Vienna, Austria",
            "",
            "https://example.com/jobs/legacy-1",
            "email-legacy-1",
            "2026-06-23T08:00:00Z",
            "Jobs",
        ),
    )
    connection.commit()
    connection.close()

    repository = JobRepository(database_path)
    try:
        repository.screen_all()
        row = repository.all()[0]
        telegram_table = repository.connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'telegram_deliveries'
            """
        ).fetchone()
    finally:
        repository.close()

    assert row["semantic_key"].startswith("semantic-")
    assert row["screening_status"] == "eligible"
    assert json.dumps(row["rejection_reasons"]) != "[]"
    assert telegram_table
