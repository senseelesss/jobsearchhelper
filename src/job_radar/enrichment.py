from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import time
import logging

from job_radar.enrichment_adapters import adapter_for
from job_radar.enrichment_adapters.common import (
    description_hash,
    description_language,
)
from job_radar.exporting import semantic_groups
from job_radar.http import HttpClient
from job_radar.storage import JobRepository
from job_radar.errors import error_label

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
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def completeness(self) -> str:
        from job_radar.enrichment_adapters.common import completeness
        return completeness(self.fetch_status, self.description, self.fetch_error)

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
        from job_radar.enrichment_adapters.ats import public_posting, application_link, parse_posting
        from job_radar.enrichment_adapters.common import structured_metadata

        target = str(row["url"])
        ats = public_posting(target)
        response = client.get(ats[1] if ats else target)
        parsed = (parse_posting(response.body, response.status_code, ats[0]) if ats
                  else adapter_for(str(row["source"])).parse(response.body, response.status_code))
        metadata = {**structured_metadata(response.body), **parsed.metadata,
                    "original_url": target, "content_url": response.final_url}
        # Only an explicit single ATS link can add one bounded public GET.
        link = application_link(response.body) if not ats and response.status_code == 200 else ""
        if link:
            endpoint = public_posting(link)
            try:
                employer = client.get(endpoint[1])
                recovered = parse_posting(employer.body, employer.status_code, endpoint[0])
                # Require matching title evidence before substituting employer content.
                from job_radar.models import normalize_semantic_text
                expected = normalize_semantic_text(str(row["title"]))
                actual = normalize_semantic_text(str(recovered.metadata.get("title", "")))
                if recovered.status == "success" and expected and actual == expected:
                    parsed, response = recovered, employer
                    metadata = {**recovered.metadata, "original_url": target,
                                "application_url": link, "content_url": employer.final_url}
            except Exception:
                # The aggregator description remains usable if the employer is unavailable.
                metadata["employer_recovery"] = "unavailable_or_budget_deferred"
    except Exception as error:
        return EnrichmentResult(
            description="",
            description_language="",
            fetched_at=fetched_at,
            fetch_status="failed",
            fetch_error=error_label(error),
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
        metadata=metadata,
    )


def _snippet_fallback(
    row: dict[str, Any], failed_result: EnrichmentResult
) -> EnrichmentResult:
    if failed_result.fetch_status == "snippet_only" and failed_result.description:
        return failed_result
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
            "completeness": _result_from_cache(group[0]).completeness,
            "attempt": repository.enrichment_attempt(group[0]["url"]),
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
    request_limit: int | None = None,
    max_seconds: float | None = None,
    ordered_keys: list[str] | None = None,
    retry_after_hours: float = 24,
    respect_cooldown: bool = False,
    max_attempts: int = 5,
) -> dict[str, Any]:
    started, requests, deferred = time.monotonic(), 0, 0
    refresh_failed = 0
    original_client = client
    source_metrics = {}
    logger = logging.getLogger(__name__)

    class BudgetClient:
        source = ""

        def get(self, url):
            nonlocal requests
            if ((request_limit is not None and requests >= request_limit)
                    or (max_seconds is not None and time.monotonic() - started >= max_seconds)):
                raise RuntimeError("Enrichment request budget exhausted")
            requests += 1
            metrics = source_metrics.setdefault(self.source, {"requests": 0, "successful_requests": 0, "http_errors": 0})
            metrics["requests"] += 1
            before = dict(getattr(original_client, "metrics", {}))
            began = time.monotonic()
            try:
                response = original_client.get(url)
                metrics["successful_requests" if response.status_code == 200 else "http_errors"] += 1
                return response
            except Exception:
                metrics["transport_errors"] = metrics.get("transport_errors", 0) + 1
                raise
            finally:
                after = getattr(original_client, "metrics", {})
                metrics["retries"] = metrics.get("retries", 0) + after.get("retries", 0) - before.get("retries", 0)
                metrics["total_latency_ms"] = metrics.get("total_latency_ms", 0) + (time.monotonic() - began) * 1000
                metrics["average_latency_ms"] = round(metrics["total_latency_ms"] / metrics["requests"], 2)

    client = BudgetClient()
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

    groups = semantic_groups(rows)
    if ordered_keys is not None:
        order = {key: index for index, key in enumerate(ordered_keys)}
        groups.sort(key=lambda group: order.get(group[0]["semantic_key"], len(order)))
    for group in groups:
        if semantic_keys is not None and str(group[0]["semantic_key"]) not in semantic_keys:
            continue
        canonical = group[0]
        current_status = str(canonical["fetch_status"])
        has_valid_description = (
            current_status == "success" and bool(canonical["description"]) and fresh(canonical)
            and canonical["description_confidence"] in {"medium", "high"}
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
        budget_hit = False
        for candidate in group:
            url = str(candidate["url"])
            attempt = repository.enrichment_attempt(url)
            if respect_cooldown and not refresh and attempt and attempt["last_status"] != "success":
                if attempt["attempts"] >= max_attempts or attempt["next_retry_at"] > _now():
                    deferred += 1
                    continue
            client.source = str(candidate["source"])
            cached = repository.cached_enrichment(url)
            if cached and (
                (cached["fetch_status"] == "success" and fresh(cached)
                 and cached["description_confidence"] in {"medium", "high"})
                or (cached["fetch_status"] != "success" and not retry_failed and not refresh)
            ):
                result = _result_from_cache(cached)
            else:
                if ((request_limit is not None and requests >= request_limit)
                        or (max_seconds is not None and time.monotonic()-started >= max_seconds)):
                    budget_hit = True
                    break
                fetch_started = time.monotonic()
                result = _fetch(candidate, client)
                repository.record_enrichment_attempt(
                    url, result.fetch_status, (time.monotonic() - fetch_started) * 1000,
                    result.metadata, retry_after_hours,
                )
                repository.cache_enrichment(url, result.to_dict())
                metrics = source_metrics.setdefault(str(candidate["source"]), {})
                label = result.completeness.lower() + "_descriptions"
                metrics[label] = metrics.get(label, 0) + 1
                logger.info("JD retrieval", extra={"stage": "enrichment", "source": candidate["source"],
                                                "fetch_status": result.fetch_status,
                                                "completeness": result.completeness})

            if result.fetch_status == "success" and result.description:
                successful = result
                break
            if first_failure is None:
                first_failure = result

        if first_failure is None and successful is None and not budget_hit:
            continue
        if budget_hit:
            deferred += 1
            if first_failure is None:
                continue  # No request: do not poison this vacancy/cache with a fake failure.

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
        if (final_result.fetch_status != "success"
                and canonical["description"] and current_status == "success"):
            # Retain useful content after a transient refresh failure. The old
            # timestamp stays stale, so tomorrow's refresh is not skipped.
            repository.update_enrichment(str(canonical["dedupe_key"]), {
                **_result_from_cache(canonical).to_dict(),
                "fetch_error": f"Refresh failed: {final_result.fetch_status}",
            })
            if final_result.fetch_status == "expired":
                repository.update_enrichment(str(canonical["dedupe_key"]), {
                    **_result_from_cache(canonical).to_dict(),
                    "fetch_status": "expired", "fetch_error": "expired listing",
                })
                totals["expired"] += 1
            else:
                refresh_failed += 1
                totals["success"] += 1
            continue
        if final_result.fetch_status != "success":
            final_result = _snippet_fallback(canonical, final_result)
        repository.update_enrichment(
            str(canonical["dedupe_key"]), final_result.to_dict()
        )
        totals[final_result.fetch_status] += 1

    if request_limit is not None or max_seconds is not None:
        totals.update(requests=requests, deferred=deferred, refresh_failed=refresh_failed)
        totals["sources"] = source_metrics
        totals["latency_ms"] = round((time.monotonic() - started) * 1000, 2)
    return totals
