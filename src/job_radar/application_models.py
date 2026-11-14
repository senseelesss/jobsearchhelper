from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Recommendation = Literal["strong_apply", "apply", "review", "skip"]
Confidence = Literal["high", "medium", "low"]


class JobScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_score: int = Field(ge=0, le=100)
    score_confidence: Confidence
    provisional: bool
    recommendation: Recommendation
    matching_strengths: list[str]
    missing_requirements: list[str]
    risk_flags: list[str]
    role_summary: str


class ApplicationDraftContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cover_letter_language: Literal["en", "de", "unknown"]
    cover_letter: str
    recruiter_message: str


class ApplicationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score_cache_key: str
    cover_letter_language: Literal["en", "de", "unknown"]
    cover_letter: str
    recruiter_message: str
    draft_status: Literal["ready", "failed", "not_applicable"]
    draft_error: str


class FitEvidence(BaseModel):
    """A claim about compatibility, supported by literal input excerpts."""
    model_config = ConfigDict(extra="forbid")
    status: Literal["confirmed_match", "confirmed_mismatch", "unknown"]
    vacancy_evidence: list[str] = Field(max_length=4)
    candidate_evidence: list[str] = Field(max_length=4)
    explanation: str = Field(max_length=500)


class MatchDimensions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    technical: FitEvidence
    experience: FitEvidence
    education: FitEvidence
    hours: FitEvidence
    language: FitEvidence
    location: FitEvidence
    career: FitEvidence


class MatchEvaluation(JobScore):
    """v3.1 provider contract; legacy JobScore remains readable for old caches."""
    dimensions: MatchDimensions
    unknown_requirements: list[str] = Field(max_length=12)
    explanation: str = Field(max_length=1000)


def parse_job_score(raw):
    import json
    value = json.loads(raw) if isinstance(raw, str) else raw
    return (MatchEvaluation if "dimensions" in value else JobScore).model_validate(value)
