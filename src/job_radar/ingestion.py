from datetime import datetime, timezone

from job_radar.enrichment_adapters.common import description_hash, description_language
from job_radar.models import Job


def ingest_observations(repository, observations):
    previous = {row["dedupe_key"]: row for row in repository.all()}
    jobs = [
        Job(
            source=o.source,
            source_job_id=o.source_job_id,
            title=o.title,
            company=o.company,
            location=o.location,
            snippet=o.snippet,
            url=o.url,
            observed_at=o.observed_at,
            connector=o.connector,
            search_url=o.search_url,
            published_at=o.published_at,
        )
        for o in observations
    ]
    result = repository.upsert_detailed(jobs)
    for job in jobs:
        repository.record_observation(job)
    for item in observations:
        if not item.fetch_status:
            continue
        old = next(row for row in repository.all() if row["dedupe_key"] == item.dedupe_key)
        prior = previous.get(item.dedupe_key, old)
        if item.fetch_status in {"failed", "blocked", "empty"} and prior["description"]:
            fields = (
                "description",
                "description_language",
                "fetched_at",
                "fetch_status",
                "fetch_error",
                "final_url",
                "description_hash",
                "description_confidence",
                "http_status",
            )
            values = {key: prior[key] for key in fields}
            values["fetch_error"] = f"Refresh failed: {item.fetch_status}"
            repository.update_enrichment(item.dedupe_key, values)
            continue
        if item.fetch_status in {"failed", "blocked", "empty"} and old["description"]:
            continue
        values = {
            "description": item.description,
            "description_language": description_language(item.description),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "fetch_status": item.fetch_status,
            "fetch_error": item.fetch_error,
            "final_url": item.url,
            "description_hash": description_hash(item.description),
            "description_confidence": item.description_confidence,
            "http_status": None,
        }
        repository.update_enrichment(item.dedupe_key, values)
        repository.cache_enrichment(item.url, values)
    return result
