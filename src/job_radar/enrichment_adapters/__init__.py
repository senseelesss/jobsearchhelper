from job_radar.enrichment_adapters.indeed import IndeedAdapter
from job_radar.enrichment_adapters.linkedin import LinkedInAdapter
from job_radar.enrichment_adapters.karriere import KarriereAdapter


def adapter_for(source: str) -> IndeedAdapter | LinkedInAdapter | KarriereAdapter:
    if source == "karriere":
        return KarriereAdapter()
    if source == "indeed":
        return IndeedAdapter()
    if source == "linkedin":
        return LinkedInAdapter()
    raise ValueError(f"Unsupported enrichment source: {source}")


__all__ = ["adapter_for"]
