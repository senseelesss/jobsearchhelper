from dataclasses import asdict, dataclass, field
from typing import Literal, Protocol

from job_radar.http import HttpResponse


class PageClient(Protocol):
    def get(self, url: str) -> HttpResponse: ...


class JobSourceAdapter(Protocol):
    """Discovery parsers consume public responses, never perform persistence/scoring."""

    def parse_search(
        self, document: str, url: str, *, status_code: int = 200, limit: int = 5,
        observed_at: str | None = None,
    ) -> "CollectionBatch": ...


@dataclass(frozen=True, slots=True)
class SourceVacancy:
    source: str
    source_job_id: str
    title: str
    company: str
    location: str
    snippet: str
    url: str
    observed_at: str
    connector: str
    description_kind: str = "snippet"
    published_at: str = ""
    search_url: str = ""
    description: str = ""
    fetch_status: str = ""
    fetch_error: str = ""
    description_confidence: str = ""

    @property
    def dedupe_key(self) -> str:
        return f"{self.source}:{self.source_job_id}"

    def to_dict(self) -> dict[str, str]:
        return {"dedupe_key": self.dedupe_key, **asdict(self)}


@dataclass(frozen=True, slots=True)
class CollectionBatch:
    status: Literal["success", "partial", "empty", "blocked", "rate_limited", "failed"]
    search_url: str
    jobs: tuple[SourceVacancy, ...] = ()
    http_status: int | None = None
    cards_seen: int = 0
    invalid_cards: int = 0
    reported_total: int | None = None
    reported_total_is_lower_bound: bool = False
    coverage_complete: bool = False
    error: str = ""
    warnings: tuple[str, ...] = ("First page only; pagination is not implemented.",)
    pages_fetched: int = 0
    stop_reason: str = ""
    metrics: dict[str, int | float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            **asdict(self),
            "jobs": [job.to_dict() for job in self.jobs],
        }
