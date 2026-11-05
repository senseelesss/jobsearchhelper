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
