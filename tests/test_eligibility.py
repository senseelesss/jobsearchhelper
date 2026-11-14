import json
from pathlib import Path

import pytest

from job_radar.daily import daily_cycle
from job_radar.eligibility import assess_application, assess_seniority, assess_location, load_policy
from job_radar.enrichment_adapters.common import extract_json_ld_description
from job_radar.screening import screen_job
from job_radar.storage import JobRepository
from job_radar.work_hours import assess_hours
from job_radar.workbook import export_workbook
from test_production import job, Provider, Telegram, SEARCH
from job_radar.sources.base import CollectionBatch

CASES = json.loads((Path(__file__).parent / "fixtures/application_priority.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda row: row["id"])
def test_anonymized_application_examples(case):
    row = {**case, "screening_status": "eligible", "fetch_status": "", "description_confidence": ""}
    result = assess_application(row)
    assert result["application_priority"] == case["expected"]
    assert result["technical_match_score"] == case.get("match_score")
    assert bool(result["exclusion_reason"]) == (case["expected"] == "EXCLUDED")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("20 Wochenstunden", "confirmed_target"),
        ("20 Std./Woche", "confirmed_target"),
        ("Junior Developer 25h/Woche", "confirmed_target"),
        ("Vollzeit (38,5 Stunden)", "confirmed_mismatch"),
        ("40 hours/week", "confirmed_mismatch"),
        ("Full-time only", "confirmed_mismatch"),
        ("Part-time possible", "flexible_part_time"),
        ("Vollzeit oder Teilzeit", "flexible_part_time"),
        ("Teilzeit 20–30 Wochenstunden", "confirmed_target"),
        ("15 Stunden pro Woche", "acceptable"),
        ("30 hours per week", "acceptable"),
        ("10 hours/week", "confirmed_mismatch"),
        ("Werkstudent Softwareentwicklung", "unknown"),
        ("Working Student Developer", "unknown"),
        ("Geringfügig", "flexible_part_time"),
        ("part-time not possible", "confirmed_mismatch"),
        ("Teilzeit oder Vollzeit (40 Stunden)", "flexible_part_time"),
    ],
)
def test_hours_classifications(text, expected):
    assert assess_hours({"title": text})["hours_fit"] == expected


def test_conflicting_hours_are_review_not_confirmed_part_time():
    row = {"title": "Junior Developer 20h/Woche", "description": "Full-time only. 40 hours/week."}
    result = assess_application(row)
    assert result["hours_fit"] == "unknown"
    assert result["hours_uncertain"]
    assert len(result["hours_evidence_details"]) >= 2
    assert result["evidence_quality"] == "conflicting"
    assert result["application_priority"] == "LOW"


def test_generic_salary_and_employer_hours_do_not_prove_role_hours():
    result = assess_hours(
        {
            "title": "Junior Python Developer",
            "description": "Gehalt auf Vollzeitbasis 38,5 Stunden pro Woche. Our employees can work part-time.",
        }
    )
    assert result["hours_fit"] == "unknown"
    assert any(
        item["source"].endswith("reference_only") for item in result["hours_evidence_details"]
    )


def test_saved_dataset_edge_cases_do_not_create_false_constraints():
    student = assess_application(
        {
            "title": "Student Support - Agile Delivery - Marketing Technology (25h/week)",
            "location": "Wien",
            "description": "Python API support for delivery teams.",
        }
    )
    assert student["application_priority"] == "MEDIUM"
    assert student["application_eligibility"] != "ineligible"
    salary = assess_hours(
        {
            "title": "Python Developer",
            "description": "Bei einer Vollzeitbeschäftigung von 38,5 Wochenstunden ist ein Jahresbruttogehalt vorgesehen.",
        }
    )
    assert salary["hours_fit"] == "unknown"
    wireless = assess_application({"title": "5G Wireless Integration Engineer", "location": "Wien"})
    assert wireless["application_priority"] == "LOW"


def test_structured_hours_and_fulltime_alternative():
    assert (
        assess_hours({"title": "Developer", "hours_min": 20, "hours_max": 30})["hours_fit"]
        == "confirmed_target"
    )
    assert (
        assess_hours({"title": "Developer", "employment_type": ["FULL_TIME", "PART_TIME"]})[
            "hours_fit"
        ]
        == "flexible_part_time"
    )
    description = extract_json_ld_description(
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@type": "JobPosting",
                "description": "Build Python APIs.",
                "employmentType": "FULL_TIME",
                "workHours": "38.5 hours/week",
            }
        )
        + "</script>"
    )
    assert (
        assess_hours({"title": "Developer", "description": description})["hours_fit"]
        == "confirmed_mismatch"
    )
    assert assess_hours({"title": "Werkstudent (40 Stunden/Monat)"})["hours_fit"] == "unknown"


@pytest.mark.parametrize("company", ["Siemens", "Helvetia", "AWS", "Example Employer"])
def test_company_names_do_not_override_role_evidence(company):
    assert (
        assess_application(
            {
                "company": company,
                "title": "Junior Python Developer (25h/Woche)",
                "location": "Vienna",
            }
        )["application_priority"]
        == "HIGH"
    )
    assert (
        assess_application(
            {"company": company, "title": "Senior Software Engineer", "location": "Vienna"}
        )["application_priority"]
        == "EXCLUDED"
    )


@pytest.mark.parametrize(
    "text,fit",
    [
        ("0–2 years of experience required", "compatible"),
        ("1–2 years experience", "compatible"),
        ("3 years experience required", "possible"),
        ("4+ years professional experience required", "mismatch"),
        ("5 years experience preferred", "unspecified"),
        ("Mindestens vier Jahre Berufserfahrung erforderlich", "mismatch"),
        ("Work alongside senior colleagues", "unspecified"),
        ("Our company has 20 years of experience", "unspecified"),
    ],
)
def test_experience_requirements_are_scoped(text, fit):
    assert (
        assess_seniority({"title": "Software Engineer", "description": text})["seniority_fit"]
        == fit
    )


def test_preferred_experience_does_not_hard_reject_screening():
    assert (
        assess_seniority(
            {
                "title": "Software Engineer",
                "description": "You will manage Python APIs within our team.",
            }
        )["seniority_fit"]
        == "unspecified"
    )
    assert (
        assess_seniority(
            {"title": "Software Engineer", "description": "You will lead our engineering team."}
        )["seniority_fit"]
        == "mismatch"
    )
    assert (
        screen_job(
            "Junior Python Developer", "Vienna", "5 years experience preferred, not required."
        ).status
        == "eligible"
    )
    assert (
        screen_job(
            "Software Engineer", "Vienna", "4 years professional experience required."
        ).status
        == "rejected"
    )
    assert (
        screen_job("Junior Developer", "Vienna", "Work with senior engineers.").status == "eligible"
    )
    assert (
        assess_seniority(
            {
                "title": "Software Engineer",
                "description": "4 years experience required, Docker preferred.",
            }
        )["seniority_fit"]
        == "mismatch"
    )


def test_remote_work_from_austria_is_not_assumed():
    assert (
        assess_location(
            {
                "location": "Berlin, Germany",
                "description": "Remote work is not available. Onsite only.",
            }
        )["location_fit"]
        == "mismatch"
    )
    assert assess_location({"location": "Remote - Germany only"})["location_fit"] == "mismatch"
    assert assess_location({"location": "EU remote"})["location_fit"] == "eu_remote_unverified"
    assert (
        assess_location(
            {"location": "Remote", "description": "Must be based in the United States."}
        )["location_fit"]
        == "mismatch"
    )
    assert (
        assess_location(
            {"location": "EU remote"},
            {"work_authorization": {"verified": True, "eu_remote_from_austria": True}},
        )["location_fit"]
        == "eu_remote_verified"
    )
    assert (
        assess_location({"location": "Vienna", "description": "US citizenship required."})[
            "location_fit"
        ]
        == "authorization_unverified"
    )
    assert (
        assess_location(
            {"location": "EU remote", "description": "Work with clients from Austria."}
        )["location_fit"]
        == "eu_remote_unverified"
    )
    assert (
        assess_location(
            {"location": "Vienna", "description": "GERMAN citizenship required."},
            {"work_authorization": {"verified": True, "citizenships": ["DE"]}},
        )["location_fit"]
        == "vienna"
    )


def run(tmp_path, items, provider=None, limit=0, telegram=None, day="2026-11-05"):
    return daily_cycle(
        tmp_path / "jobs.db",
        [SEARCH],
        lambda url: CollectionBatch("success", url, jobs=tuple(items)),
        provider=provider,
        profile={},
        score_model="test-model",
        score_limit=limit,
        telegram=telegram,
        chat_id="chat",
        day=day,
        output=tmp_path / "out",
    )


def test_excluded_jobs_cannot_take_scoring_or_notification_budget(tmp_path):
    items = [
        job(1, title="Student Support - Agile Delivery (25h/week)"),
        job(2, title="Senior Solutions Architect - Full-time"),
        job(3, title="Junior Python Developer (20h/Woche)"),
        job(4, title="Junior Python Developer", snippet="Full-time only. 40 hours/week."),
    ]
    provider, telegram = Provider(), Telegram()
    result = run(tmp_path, items, provider, 2, telegram)
    assert len(provider.calls) == 2
    assert set(provider.calls) == {"linkedin:1", "linkedin:3"}
    assert "jobs/view/2/" not in telegram.messages[0] and "jobs/view/4/" not in telegram.messages[0]
    assert "Priority: HIGH" in telegram.messages[0]
    totals = result["funnel"]["database_totals"]
    assert sum(totals["application_priorities"].values()) == 4
    assert sum(totals["hours_fits"].values()) == 4
    assert totals["application_priorities"]["EXCLUDED"] == 2
    assert result["top_applications"][0]["dedupe_key"] == "linkedin:3"


def test_high_tier_b_outranks_low_tier_a_and_priority_changes_do_not_resend(tmp_path):
    provider, telegram = Provider(), Telegram()
    run(tmp_path, [job(1, title="Software Engineer")], provider, 1)
    with JobRepository(tmp_path / "jobs.db") as repo:
        score = repo.connection.execute("SELECT result_json FROM job_scores").fetchone()[0]
        data = json.loads(score)
        data["match_score"] = 35
        repo.connection.execute("UPDATE job_scores SET result_json=?", (json.dumps(data),))
        repo.connection.commit()
    result = run(tmp_path, [job(2, title="Junior Python Developer (20h/Woche)")], None, 0, telegram)
    assert "Tier B · UNSCORED" in telegram.messages[0]
    assert telegram.messages[0].index("jobs/view/2/") < telegram.messages[0].index("jobs/view/1/")
    run(tmp_path, [], provider, 1, telegram, day="2026-11-06")
    assert "jobs/view/2/" not in telegram.messages[1]
    assert result["preview_tiers"]["B"] == 1


def test_pending_low_backlog_gets_a_slot_amid_new_high_jobs(tmp_path):
    run(tmp_path, [job(i, title=f"Software Engineer {i}") for i in range(1, 5)])
    with JobRepository(tmp_path / "jobs.db") as repo:
        repo.connection.execute(
            "UPDATE jobs SET created_at=?",
            ("2026-10-26T00:00:00+00:00",),
        )
        repo.connection.commit()
    telegram = Telegram()
    for day, offset in [
        ("2026-11-05", 100),
        ("2026-11-06", 200),
        ("2026-11-07", 300),
        ("2026-11-08", 400),
    ]:
        result = run(
            tmp_path,
            [
                job(offset + i, title=f"Junior Python Developer {offset + i} (20h/Woche)")
                for i in range(15)
            ],
            telegram=telegram,
            day=day,
        )
        assert result["alert_items"] <= 8
        assert len(telegram.messages[-1].encode("utf-16-le")) // 2 <= 3800
    for i in range(1, 5):
        assert any(f"jobs/view/{i}/" in message for message in telegram.messages)


def test_policy_is_configurable_without_changing_ai_score(tmp_path):
    policy = json.loads(Path("src/job_radar/eligibility_rules.v1.json").read_text())
    policy["acceptable_hours"] = [15, 35]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy))
    row = {"title": "Junior Python Developer (35h/Woche)", "location": "Vienna", "match_score": 86}
    assert assess_application(row)["application_priority"] == "EXCLUDED"
    result = assess_application(row, policy=load_policy(path))
    assert result["application_priority"] == "HIGH"
    assert result["technical_match_score"] == 86


def test_workbook_priority_columns_sort_and_preserve_status_import_layout(tmp_path):
    from openpyxl import load_workbook

    rows = []
    for case in CASES[:10]:
        row = {**case, "company": "Example Employer", "dedupe_key": case["id"]}
        row.update(assess_application(row))
        rows.append(row)
    path = export_workbook(rows, tmp_path / "jobs.xlsx")
    wb = load_workbook(path)
    sheet = wb["Вакансії"]
    headers = [cell.value for cell in sheet[5]]
    for label in (
        "Пріоритет подачі",
        "Відповідність годин",
        "Відповідність рівня",
        "Можливість подачі",
        "Якість доказів",
        "Причина виключення",
    ):
        assert label in headers
    assert sheet["D6"].value == "HIGH"
    assert wb["Зміни статусів"]["A5"].value == "ID вакансії"
    assert wb["Зміни статусів"]["C5"].value == "Новий статус"
    assert sheet.auto_filter.ref.endswith(f"{sheet.max_row}")
    wb.close()
