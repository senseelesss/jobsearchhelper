"""Validate AI evidence and enforce the existing application policy after scoring."""
import json

from job_radar.application_models import MatchEvaluation
from job_radar.eligibility import assess_application


def finalize_evaluation(score, vacancy, profile):
    # Old provider/test contracts remain supported, never interpreted as new evidence.
    data = score.model_dump()
    assessment = assess_application(vacancy, profile)
    blockers = [r["code"] for r in assessment["eligibility_reasons"] if r["hard"]]
    if isinstance(score, MatchEvaluation):
        description = str(vacancy.get("description") or vacancy.get("snippet") or "")
        candidate = json.dumps(profile, ensure_ascii=False)
        uncertain = False
        for name, dimension in data["dimensions"].items():
            job_quotes = dimension["vacancy_evidence"]
            candidate_quotes = dimension["candidate_evidence"]
            grounded = (bool(job_quotes) and bool(candidate_quotes)
                        and all(q.strip() and q in description for q in job_quotes)
                        and all(q.strip() and q in candidate for q in candidate_quotes))
            if dimension["status"] != "unknown" and not grounded:
                dimension.update(status="unknown", vacancy_evidence=[], candidate_evidence=[],
                                 explanation="Evidence could not be verified against the inputs.")
            if dimension["status"] == "unknown":
                uncertain = True
            # A model's interpretation remains reviewable, not a new deterministic fact.
            if dimension["status"] == "confirmed_mismatch":
                if data["recommendation"] in {"apply", "strong_apply"}:
                    data["recommendation"] = "review"
                data["risk_flags"] = list(dict.fromkeys([*data["risk_flags"], f"ai_{name}_mismatch"]))
        if uncertain:
            data["score_confidence"] = "low"
            if data["recommendation"] in {"apply", "strong_apply"}:
                if data["recommendation"] in {"apply", "strong_apply"}:
                    data["recommendation"] = "review"
    if blockers:
        data["recommendation"] = "skip"
        data["risk_flags"] = list(dict.fromkeys([*data["risk_flags"], *blockers]))
    return type(score).model_validate(data)
