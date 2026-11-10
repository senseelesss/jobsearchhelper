"""One registry for source-neutral collection dispatch."""

from job_radar.sources import karriere, linkedin, absolventen
from job_radar.sources.ats import PublicBoardAdapter
from job_radar.sources.base import JobSourceAdapter

ADAPTERS: dict[str, JobSourceAdapter] = {
    "karriere": karriere,
    "linkedin": linkedin,
    "absolventen": absolventen,
    "greenhouse": PublicBoardAdapter("greenhouse"),
    "lever": PublicBoardAdapter("lever"),
}


def source_adapter(source: str) -> JobSourceAdapter:
    try:
        return ADAPTERS[source]
    except KeyError:
        raise ValueError(f"Unsupported discovery source: {source}") from None
