import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_radar.models import normalize_semantic_text
from job_radar.eligibility import assess_location, assess_seniority, load_policy

RULES_PATH = Path(__file__).with_name("screening_rules.v4.json")


@dataclass(frozen=True, slots=True)
class ScreeningResult:
    status: str
    rejection_reasons: list[dict[str, Any]]
    rules_version: str


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _matched_terms(value: str, terms: list[str]) -> list[str]:
    matches: list[str] = []
    for term in terms:
        normalized_term = normalize_semantic_text(term)
        if re.search(rf"\b{re.escape(normalized_term)}\b", value):
            matches.append(term)
    return matches


def screen_job(
    title: str,
    location: str,
    snippet: str = "",
    rules: dict[str, Any] | None = None,
    fetch_status: str = "",
    fetch_error: str = "",
) -> ScreeningResult:
    active_rules = rules or load_rules()
    normalized_title = normalize_semantic_text(title)
    normalized_context = normalize_semantic_text(f"{title} {snippet}")
    normalized_location = normalize_semantic_text(location)
    reasons: list[dict[str, Any]] = []

    if fetch_status == "expired" or fetch_error.casefold() == "expired listing":
        reasons.append({"code": "expired_vacancy", "severity": "hard"})

    junior_matches = _matched_terms(normalized_title, active_rules["junior_context_terms"])
    seniority_matches = _matched_terms(normalized_title, active_rules["hard_seniority_terms"])
    seniority = assess_seniority({"title": title, "description": snippet})
    if (seniority_matches and not junior_matches) or (
        seniority["seniority_fit"] == "mismatch"
        and seniority["seniority_level"] in {"manager", "architect", "lead"}
    ):
        reasons.append(
            {
                "code": "seniority",
                "severity": "hard",
                "matched_terms": seniority_matches,
            }
        )

    if (
        seniority["required_years"]
        and seniority["required_years"] >= load_policy()["excluded_required_years"]
    ):
        reasons.append(
            {
                "code": "excessive_experience",
                "severity": "hard",
                "matched_text": "; ".join(seniority["seniority_evidence"])[:400],
            }
        )

    irrelevant_matches = _matched_terms(
        normalized_title, active_rules["irrelevant_profession_terms"]
    )
    if irrelevant_matches:
        reasons.append(
            {
                "code": "irrelevant_profession",
                "severity": "hard",
                "matched_terms": irrelevant_matches,
            }
        )

    vienna_matches = _matched_terms(normalized_location, active_rules["vienna_terms"])
    remote_matches = _matched_terms(normalized_location, active_rules["remote_terms"])
    austria_matches = _matched_terms(normalized_location, active_rules["austria_terms"])
    detailed_location = assess_location({"location": location, "description": snippet})["location_fit"]
    location_is_accepted = bool(vienna_matches or (remote_matches and austria_matches)
                                or detailed_location == "austria_remote")
    if detailed_location == "mismatch":
        location_is_accepted = False
    location_is_unknown = not normalized_location or (remote_matches and not austria_matches)
    if not location_is_accepted and (not location_is_unknown or detailed_location == "mismatch"):
        reasons.append(
            {
                "code": "location_outside_target",
                "severity": "hard",
                "location": location,
            }
        )

    if any(reason["severity"] == "hard" for reason in reasons):
        status = "rejected"
    else:
        priority_matches = _matched_terms(normalized_context, active_rules["priority_terms"])
        technical_matches = _matched_terms(
            normalized_context, active_rules["relevant_technical_terms"]
        )
        technical_title_matches = _matched_terms(
            normalized_title, active_rules["relevant_technical_terms"]
        )
        adjacent_matches = _matched_terms(normalized_title, active_rules["adjacent_review_terms"])
        always_review_matches = _matched_terms(
            normalized_title, active_rules["always_review_terms"]
        )
        if always_review_matches or (adjacent_matches and not technical_title_matches):
            status = "review"
            reasons.append(
                {
                    "code": "adjacent_role",
                    "severity": "review",
                    "matched_terms": always_review_matches or adjacent_matches,
                }
            )
        elif technical_matches and location_is_accepted:
            status = "eligible"
            reasons.append(
                {
                    "code": "relevant_technical_role",
                    "severity": "positive",
                    "matched_terms": technical_matches,
                }
            )
        elif priority_matches and location_is_accepted:
            status = "review"
            reasons.append(
                {
                    "code": "early_career_adjacent",
                    "severity": "review",
                    "matched_terms": priority_matches,
                }
            )
        else:
            status = "review"
            reasons.append(
                {
                    "code": "plausible_or_incomplete",
                    "severity": "review",
                }
            )

    return ScreeningResult(
        status=status,
        rejection_reasons=reasons,
        rules_version=active_rules["version"],
    )
