from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from job_radar.application_models import ApplicationDraftContent, JobScore


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ScoreProviderResult:
    score: JobScore
    usage: TokenUsage
    response_id: str = ""


@dataclass(frozen=True, slots=True)
class DraftProviderResult:
    draft: ApplicationDraftContent
    usage: TokenUsage
    response_id: str = ""


class AIProvider(Protocol):
    model: str

    def score(
        self,
        candidate_profile: dict[str, Any],
        vacancy: dict[str, Any],
        provisional: bool,
    ) -> ScoreProviderResult: ...

    def draft(
        self,
        candidate_profile: dict[str, Any],
        vacancy: dict[str, Any],
        score: JobScore,
    ) -> DraftProviderResult: ...

    def rewrite_draft(
        self,
        candidate_profile: dict[str, Any],
        vacancy: dict[str, Any],
        score: JobScore,
        draft: ApplicationDraftContent,
    ) -> DraftProviderResult: ...
