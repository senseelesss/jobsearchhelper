from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from job_radar.enrichment_adapters import adapter_for
from job_radar.enrichment_adapters.common import (
    description_hash,
    description_language,
)
from job_radar.exporting import semantic_groups
from job_radar.http import HttpClient
from job_radar.storage import JobRepository

FETCH_STATUSES = (
    "success",
    "snippet_only",
    "expired",
    "blocked",
    "empty",
    "failed",
)


@dataclass(frozen=True, slots=True)
class EnrichmentResult:
    description: str
    description_language: str
    fetched_at: str
    fetch_status: str
    fetch_error: str
    final_url: str
    description_hash: str
    description_confidence: str
    http_status: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result_from_cache(values: dict[str, Any]) -> EnrichmentResult:
    return EnrichmentResult(
        description=values["description"],
        description_language=values["description_language"],
        fetched_at=values["fetched_at"],
        fetch_status=values["fetch_status"],
        fetch_error=values["fetch_error"],
        final_url=values["final_url"],
        description_hash=values["description_hash"],
        description_confidence=values["description_confidence"],
        http_status=values["http_status"],
    )


def _fetch(
    row: dict[str, Any],
    client: HttpClient,
) -> EnrichmentResult:
    fetched_at = _now()
    try:
        response = client.get(str(row["url"]))
        parsed = adapter_for(str(row["source"])).parse(
            response.body, response.status_code
        )
    except (RuntimeError, ValueError) as error:
        return EnrichmentResult(
            description="",
            description_language="",
            fetched_at=fetched_at,
            fetch_status="failed",
            fetch_error=str(error),
            final_url=str(row["url"]),
            description_hash="",
            description_confidence="",
            http_status=None,
        )

    return EnrichmentResult(
        description=parsed.description,
        description_language=parsed.description_language,
        fetched_at=fetched_at,
        fetch_status=parsed.status,
        fetch_error=parsed.error,
        final_url=response.final_url,
        description_hash=(
            description_hash(parsed.description) if parsed.description else ""
        ),
        description_confidence=parsed.description_confidence,
        http_status=response.status_code,
    )


def _snippet_fallback(
    row: dict[str, Any], failed_result: EnrichmentResult
) -> EnrichmentResult:
    snippet = str(row["snippet"]).strip()
    if not snippet:
        return failed_result
    fallback_errors = {
        "expired": "expired listing",
        "blocked": "blocked page",
        "empty": "empty description",
        "failed": "fetch failed",
    }
    return EnrichmentResult(
        description=snippet,
        description_language=description_language(snippet),
        fetched_at=failed_result.fetched_at,
        fetch_status="snippet_only",
        fetch_error=(
            failed_result.fetch_error
            or fallback_errors.get(failed_result.fetch_status, "")
        ),
        final_url=failed_result.final_url,
        description_hash=description_hash(snippet),
        description_confidence="low",
        http_status=failed_result.http_status,
    )


def enrichment_report(
    repository: JobRepository, only: str = "eligible"
) -> list[dict[str, Any]]:
    return [
        {
            "source": group[0]["source"],
            "title": group[0]["title"],
            "fetch_status": group[0]["fetch_status"],
            "fetch_error": group[0]["fetch_error"],
            "http_status": group[0]["http_status"],
            "final_url": group[0]["final_url"] or group[0]["url"],
        }
        for group in semantic_groups(repository.all(only))
    ]


def enrich_jobs(
    repository: JobRepository,
    client: HttpClient,
    only: str | None = None,
    retry_failed: bool = False,
    semantic_keys: set[str] | None = None,
    max_age_hours: float = 24,
    refresh: bool = False,
) -> dict[str, int]:
    def fresh(values) -> bool:
        if refresh:
            return False
        try:
            stamp = datetime.fromisoformat(str(values["fetched_at"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return 0 <= (datetime.now(timezone.utc) - stamp).total_seconds() < max_age_hours * 3600
        except (ValueError, KeyError):
            return False
    statuses = ("eligible", "review") if only is None else (only,)
    rows = [
        row
        for status in statuses
        for row in repository.all(status)
    ]
    totals = dict.fromkeys(FETCH_STATUSES, 0)

    for group in semantic_groups(rows):
        if semantic_keys is not None and str(group[0]["semantic_key"]) not in semantic_keys:
            continue
        canonical = group[0]
        current_status = str(canonical["fetch_status"])
        has_valid_description = (
            current_status == "success" and bool(canonical["description"]) and fresh(canonical)
        )
        if has_valid_description:
            refreshed = {
                "description": str(canonical["description"]),
                "description_language": description_language(
                    str(canonical["description"])
                ),
                "fetched_at": str(canonical["fetched_at"]),
                "fetch_status": "success",
                "fetch_error": str(canonical["fetch_error"]),
                "final_url": str(canonical["final_url"]),
                "description_hash": description_hash(
                    str(canonical["description"])
                ),
                "description_confidence": (
                    str(canonical["description_confidence"]) or "medium"
                ),
                "http_status": canonical["http_status"],
            }
            repository.update_enrichment(
                str(canonical["dedupe_key"]), refreshed
            )
            totals["success"] += 1
            continue
        if (
            current_status == "snippet_only"
            and canonical["description"]
            and not retry_failed
            and not refresh
        ):
            totals["snippet_only"] += 1
            continue
        if current_status in FETCH_STATUSES and current_status != "success" and not retry_failed and not refresh:
            fallback = _snippet_fallback(
                canonical,
                EnrichmentResult(
                    description="",
                    description_language="",
                    fetched_at=str(canonical["fetched_at"]) or _now(),
                    fetch_status=current_status,
                    fetch_error=str(canonical["fetch_error"]),
                    final_url=str(canonical["final_url"]) or str(canonical["url"]),
                    description_hash="",
                    description_confidence="",
                    http_status=canonical["http_status"],
                ),
            )
            repository.update_enrichment(
                str(canonical["dedupe_key"]), fallback.to_dict()
            )
            totals[fallback.fetch_status] += 1
            continue

        first_failure: EnrichmentResult | None = None
        successful: EnrichmentResult | None = None
        for candidate in group:
            url = str(candidate["url"])
            cached = repository.cached_enrichment(url)
            if cached and (
                (cached["fetch_status"] == "success" and fresh(cached))
                or (cached["fetch_status"] != "success" and not retry_failed and not refresh)
            ):
                result = _result_from_cache(cached)
            else:
                result = _fetch(candidate, client)
                repository.cache_enrichment(url, result.to_dict())

            if result.fetch_status == "success" and result.description:
                successful = result
                break
            if first_failure is None:
                first_failure = result

        final_result = successful or first_failure or EnrichmentResult(
            description="",
            description_language="",
            fetched_at=_now(),
            fetch_status="failed",
            fetch_error="No source URL available",
            final_url="",
            description_hash="",
            description_confidence="",
            http_status=None,
        )
        if (final_result.fetch_status in {"failed", "blocked", "empty"}
                and canonical["description"] and current_status == "success"):
            # Retain useful content after a transient refresh failure. The old
            # timestamp stays stale, so tomorrow's refresh is not skipped.
            repository.update_enrichment(str(canonical["dedupe_key"]), {
                **_result_from_cache(canonical).to_dict(),
                "fetch_error": f"Refresh failed: {final_result.fetch_status}",
            })
            totals["success"] += 1
            continue
        if final_result.fetch_status != "success":
            final_result = _snippet_fallback(canonical, final_result)
        repository.update_enrichment(
            str(canonical["dedupe_key"]), final_result.to_dict()
        )
        totals[final_result.fetch_status] += 1

    return totals
