"""Bounded collection, details, and partial-failure preservation."""

import time
from dataclasses import replace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_radar.enrichment_adapters import adapter_for
from job_radar.searches import validate_search_url
from job_radar.sources import karriere, linkedin
from job_radar.sources.base import CollectionBatch


def _http_pages(client, search, max_pages):
    parsed = urlsplit(search.url)
    initial = dict(parse_qsl(parsed.query))
    offset = int(initial.get("start", "0"))
    first_page = int(initial.get("page", "1"))
    for index in range(max_pages):
        pairs = [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k not in {"page", "start"}
        ]
        if index:
            pairs.append(
                (
                    "page" if search.source == "karriere" else "start",
                    str(first_page + index if search.source == "karriere" else offset),
                )
            )
        url = search.url if not index else urlunsplit(parsed._replace(query=urlencode(pairs)))
        if index and search.source == "linkedin":
            url = urlunsplit(
                parsed._replace(
                    path="/jobs-guest/jobs/api/seeMoreJobPostings/search", query=urlencode(pairs)
                )
            )
        response = client.get(url)
        yield response
        if search.source == "linkedin":
            offset += linkedin.parse_search(response.body, search.url, limit=500).cards_seen


def collect_search(
    client, url: str, *, limit=25, max_pages=1, details=0, max_seconds=120
) -> CollectionBatch:
    search = validate_search_url(url)
    if not 1 <= limit <= 500 or not 1 <= max_pages <= 10 or not 0 <= details <= 25:
        raise ValueError("Limits: jobs 1–500, pages 1–10, details 0–25")
    started = time.monotonic()
    parser = {"karriere": karriere.parse_search, "linkedin": linkedin.parse_search}[search.source]
    pages = (
        client.iter_search_pages(search.url, search.source, max_pages)
        if hasattr(client, "iter_search_pages")
        else _http_pages(client, search, max_pages)
    )
    jobs = {}
    latest = CollectionBatch("failed", search.url)
    fetched = invalid = cards = 0
    stop = "page_limit"
    errors = []
    complete = False
    try:
        for response in pages:
            fetched += 1
            final = urlsplit(response.final_url)
            allowed = (
                {"www.karriere.at", "karriere.at"}
                if search.source == "karriere"
                else {"www.linkedin.com", "at.linkedin.com", "linkedin.com"}
            )
            paths = {urlsplit(search.url).path.rstrip("/")}
            if search.source == "linkedin" and fetched > 1:
                paths.add("/jobs-guest/jobs/api/seeMoreJobPostings/search")
            if (
                final.hostname not in allowed
                or final.scheme != "https"
                or final.path.rstrip("/") not in paths
            ):
                latest = replace(latest, status="blocked", error="Unexpected search redirect")
                stop = "blocked"
                break
            latest = parser(response.body, search.url, status_code=response.status_code, limit=500)
            cards += latest.cards_seen
            invalid += latest.invalid_cards
            if latest.status not in {"success", "partial", "empty"}:
                errors.append(latest.error)
                stop = latest.status
                break
            added = 0
            for job in latest.jobs:
                if job.dedupe_key not in jobs:
                    jobs[job.dedupe_key] = replace(job, search_url=search.url)
                    added += 1
            if latest.status == "partial":
                errors.append(f"Page {fetched}: invalid cards")
            if not added and latest.status != "empty":
                stop = "repeated_page"
                errors.append("No new native IDs; pagination may be ignored by this transport")
                break
            if latest.status == "empty" or (
                latest.reported_total is not None
                and not latest.reported_total_is_lower_bound
                and len(jobs) >= latest.reported_total
            ):
                complete, stop = True, "end_of_results"
                break
            if len(jobs) >= limit:
                stop = "job_limit"
                break
            if time.monotonic() - started >= max_seconds:
                stop = "time_limit"
                errors.append("Search time budget reached")
                break
        else:
            stop = "navigation_end" if fetched < max_pages else "page_limit"
    except Exception as error:
        errors.append(f"Page transport failure: {type(error).__name__}")
        stop = "transport_failure"
    finally:
        if hasattr(pages, "close"):
            pages.close()

    selected = list(jobs.values())[:limit]
    complete = complete and len(selected) == len(jobs) and not errors
    for index, job in enumerate(selected[:details]):
        if time.monotonic() - started >= max_seconds:
            errors.append("Detail time budget reached")
            break
        try:
            response = client.get(job.url)
            final = urlsplit(response.final_url)
            expected = urlsplit(job.url)
            same_path = final.path.rstrip("/") == expected.path.rstrip("/")
            if job.source == "linkedin":
                import re

                same_path = bool(
                    re.fullmatch(rf"/jobs/view/(?:[^/]*-)?{job.source_job_id}/?", final.path)
                )
            detail_hosts = {expected.hostname}
            if job.source == "linkedin":
                detail_hosts.add("at.linkedin.com")
            if final.scheme != "https" or final.hostname not in detail_hosts or not same_path:
                raise ValueError("Unexpected detail redirect")
            result = adapter_for(job.source).parse(response.body, response.status_code)
            selected[index] = replace(
                job,
                description=result.description,
                fetch_status=result.status,
                fetch_error=result.error,
                description_confidence=result.description_confidence,
                description_kind="full" if result.status == "success" else job.description_kind,
            )
            if result.status != "success":
                errors.append(f"{job.dedupe_key}: detail {result.status}")
        except Exception as error:
            selected[index] = replace(job, fetch_status="failed", fetch_error=type(error).__name__)
            errors.append(f"{job.dedupe_key}: detail transport failed")
    if selected:
        status = (
            "partial"
            if errors or latest.status in {"failed", "blocked", "rate_limited"}
            else "success"
        )
    else:
        status = "empty" if latest.status == "empty" and not errors else latest.status
        if status in {"success", "partial"} or errors and status == "empty":
            status = "failed"
    return replace(
        latest,
        status=status,
        jobs=tuple(selected),
        pages_fetched=fetched,
        cards_seen=cards,
        invalid_cards=invalid,
        stop_reason=stop,
        coverage_complete=complete,
        error="; ".join(errors) or latest.error,
        warnings=("Coverage is bounded by configured budgets; absence is not expiry.",),
    )
