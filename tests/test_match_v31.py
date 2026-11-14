"""Synthetic policy contracts, not human-labelled market accuracy estimates."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from job_radar.application_models import MatchEvaluation, parse_job_score
from job_radar.eligibility import assess_application, evaluation_candidate
from job_radar.match_policy import finalize_evaluation
from job_radar.matching import score_jobs, draft_jobs, application_queue
from job_radar.candidate import candidate_profile_hash
from job_radar.storage import JobRepository
from job_radar.screening import screen_job
from test_matching import FakeProvider, insert_job, score

DIMENSIONS = ("technical", "experience", "education", "hours", "language", "location", "career")


def evaluation(status="unknown", job_quotes=None, candidate_quotes=None):
    return MatchEvaluation(**score().model_dump(), dimensions={
        key: {"status": status, "vacancy_evidence": job_quotes or [],
              "candidate_evidence": candidate_quotes or [], "explanation": "Fixture interpretation"}
        for key in DIMENSIONS}, explanation="Fixture review", unknown_requirements=[])


def row(text="", title="Junior Python Developer", location="Vienna"):
    return {"title": title, "description": text, "location": location,
            "screening_status": "eligible", "application_state": "not_applied"}


@pytest.mark.parametrize("text,excluded", [
    ("Full-time only. 38.5 hours per week.", True),
    ("40 hours per week. No part-time.", True),
    ("20–25 hours per week.", False),
    ("Part-time, 30 hours per week.", False),  # Default acceptable range includes 30.
    ("Salary on full-time equivalent basis, 40 hours per week.", False),
    ("Python APIs; hours not stated.", False),
])
def test_hours_constraints(text, excluded):
    assert (assess_application(row(text))["application_eligibility"] == "ineligible") == excluded


def test_thirty_hours_excluded_only_with_confirmed_preference_override():
    profile = {"search_preferences": {"target_hours": [20, 25], "acceptable_hours": [20, 25]}}
    assert assess_application(row("30 hours per week"), profile)["application_eligibility"] == "ineligible"


@pytest.mark.parametrize("text,hard", [
    ("Completed Master's degree required", True),
    ("Master's degree preferred", False),
    ("Master's degree or equivalent experience required", False),
    ("Bachelor or Master required", False),
    ("Enrolled in a Master's degree", False),
])
def test_degree_requirements_use_confirmed_facts(text, hard):
    profile = {"verified_qualifications": {"confirmed": True, "completed_degrees": ["bachelor"]}}
    result = assess_application(row(text), profile)
    assert any(r["code"] == "education_mismatch" for r in result["eligibility_reasons"]) == hard


def test_missing_education_is_unknown_not_mismatch():
    result = assess_application(row("Completed Master's degree required"), {})
    assert result["application_eligibility"] == "review_required"
    assert any(r["code"] == "education_review" for r in result["eligibility_reasons"])


@pytest.mark.parametrize("years", [3, 4, 5])
def test_experience_constraint(years):
    profile = {"verified_qualifications": {"confirmed": True, "professional_experience_years": 1}}
    result = assess_application(row(f"Minimum {years} years of professional experience required"), profile)
    assert result["application_eligibility"] == "ineligible"


@pytest.mark.parametrize("title,allowed", [
    ("Business Consultant", False), ("Industrial Automation Engineer", False),
    ("Mechanical Engineer", False), ("Software Automation Engineer", True),
    ("Werkstudent Softwareentwicklung", True), ("Intern Backend Developer", True),
    ("Junior Java Developer", True),
])
def test_relevance_spend_gate(title, allowed):
    job = row("SQL, integration and automation", title=title)
    job.update(assess_application(job))
    assert evaluation_candidate(job) == allowed


def test_suburb_is_not_rejected_as_known_incompatible():
    assert screen_job("Junior Python Developer", "Mödling", "Python").status == "review"
    assert assess_application(row(location="Mödling"))["application_eligibility"] == "review_required"


def test_unverified_ai_claims_are_downgraded():
    result = finalize_evaluation(evaluation("confirmed_match", ["invented"], ["invented"]), row("Python"), {})
    assert result.recommendation == "review"
    assert result.score_confidence == "low"
    assert all(d.status == "unknown" for d in result.dimensions.__dict__.values())


def test_high_ai_score_cannot_override_hard_hours():
    result = finalize_evaluation(evaluation(), row("Full-time only. 40 hours per week"), {})
    assert result.recommendation == "skip"
    assert "hours_mismatch" in result.risk_flags


def test_new_schema_requires_every_dimension_and_rejects_unknown_keys():
    data = evaluation().model_dump()
    del data["dimensions"]["education"]
    with pytest.raises(ValidationError):
        MatchEvaluation.model_validate(data)
    data = evaluation().model_dump()
    data["dimensions"]["technical"]["status"] = "probably"
    with pytest.raises(ValidationError):
        MatchEvaluation.model_validate(data)


def test_new_scores_survive_cache_restart_and_drafting(tmp_path):
    path = tmp_path / "jobs.db"
    provider = FakeProvider(evaluation())
    profile = {"core_skills": ["Python"]}
    with JobRepository(path) as repository:
        insert_job(repository, "linkedin", "v31", screening_status="eligible", fetch_status="success",
                   description="Python APIs, 20 hours per week")
        assert score_jobs(repository, provider, profile).scored == 1
        assert score_jobs(repository, provider, profile).cached == 1
        assert provider.score_calls == 1
        assert draft_jobs(repository, provider, profile).ready == 1
    with JobRepository(path) as repository:
        records = application_queue(repository, all_active=True, profile_hash=candidate_profile_hash(profile), model=provider.model)
        assert records[0]["dimensions"]["education"]["status"] == "unknown"
        stored = repository.connection.execute("SELECT result_json FROM job_scores").fetchone()[0]
        assert isinstance(parse_job_score(stored), MatchEvaluation)
        assert score_jobs(repository, provider, {"core_skills": ["Python", "SQL"]}).scored == 1


def test_preserved_regression_dataset_has_no_new_false_exclusions():
    cases = json.loads((Path(__file__).parent / "fixtures/application_priority.json").read_text())
    for case in cases:
        actual = assess_application({**case, "screening_status": "eligible"})
        assert (actual["application_priority"] == "EXCLUDED") == (case["expected"] == "EXCLUDED")


def test_ai_mismatch_does_not_promote_skip():
    result = evaluation("confirmed_mismatch", ["Python"], ["Python"])
    result.recommendation = "skip"
    assert finalize_evaluation(result, row("Python"), {"skills": ["Python"]}).recommendation == "skip"


def test_negated_degree_requirement_is_not_a_blocker():
    profile = {"verified_qualifications": {"confirmed": True, "completed_degrees": []}}
    result = assess_application(row("A Master's degree is not required"), profile)
    assert not any(r["code"].startswith("education_") for r in result["eligibility_reasons"])


def test_scrum_master_is_not_a_masters_degree():
    profile = {"verified_qualifications": {"confirmed": True, "completed_degrees": []}}
    result = assess_application(row("Completed Scrum Master training required"), profile)
    assert not any(r["code"].startswith("education_") for r in result["eligibility_reasons"])
