"""Documented public employer board feeds. Board identifiers must be configured."""

import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

from job_radar.enrichment_adapters.ats import parse_posting, public_posting
from job_radar.sources.base import CollectionBatch, SourceVacancy


class PublicBoardAdapter:
    def __init__(self, source: str):
        self.source = source

    def parse_search(
        self,
        document: str,
        url: str,
        *,
        status_code: int = 200,
        limit: int = 5,
        observed_at: str | None = None,
    ) -> CollectionBatch:
        if status_code != 200:
            return CollectionBatch(
                "rate_limited"
                if status_code == 429
                else "blocked"
                if status_code in {401, 403}
                else "failed",
                url,
                http_status=status_code,
                error=f"HTTP {status_code}",
            )
        try:
            value = json.loads(document)
            items = value["jobs"] if self.source == "greenhouse" else value
            if not isinstance(items, list):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            return CollectionBatch(
                "failed", url, http_status=200, error="Malformed public board response"
            )
        board = urlsplit(url).path.split("/")[3]
        jobs, invalid = {}, 0
        for item in items:
            try:
                identity = str(item["id"])
                title = item["title" if self.source == "greenhouse" else "text"]
                target = item["absolute_url" if self.source == "greenhouse" else "hostedUrl"]
                parsed = urlsplit(target)
                endpoint = public_posting(target)
                if (
                    not endpoint
                    or endpoint[0] != self.source
                    or parsed.path.split("/")[1] != board
                    or not isinstance(title, str)
                    or not title.strip()
                    or not identity
                ):
                    raise ValueError()
                location = (
                    item.get("location", {}).get("name", "")
                    if self.source == "greenhouse"
                    else item.get("categories", {}).get("location", "")
                )
                detail = parse_posting(json.dumps(item), 200, self.source)
                job = SourceVacancy(
                    source=self.source,
                    source_job_id=f"{board}:{identity}",
                    title=title,
                    # Board token is provenance, not an invented employer display name.
                    company=item.get("company_name", ""),
                    location=location,
                    snippet=(
                        "Employment type: " + str(item.get("categories", {}).get("commitment", ""))
                        if self.source == "lever"
                        else ""
                    ),
                    url=target,
                    observed_at=observed_at or datetime.now(timezone.utc).isoformat(),
                    published_at=str(item.get("updated_at", "")),
                    connector=f"{self.source}-public-api-v1",
                    description=detail.description,
                    fetch_status=detail.status,
                    fetch_error=detail.error,
                    description_confidence=detail.description_confidence,
                    description_kind="full" if detail.status == "success" else "snippet",
                )
                jobs.setdefault(job.dedupe_key, job)
            except (KeyError, TypeError, ValueError, AttributeError):
                invalid += 1
        return CollectionBatch(
            "partial"
            if invalid and jobs
            else "failed"
            if invalid
            else "success"
            if jobs
            else "empty",
            url,
            jobs=tuple(jobs.values())[:limit],
            http_status=200,
            cards_seen=len(items),
            invalid_cards=invalid,
            reported_total=len(items),
            error="Invalid public board entries" if invalid else "",
            warnings=(),
        )
