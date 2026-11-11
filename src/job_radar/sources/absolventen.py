"""Public, filterable Job Manager RSS. A feed window never proves full coverage."""

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from job_radar.enrichment_adapters.common import classify_common
from job_radar.sources.base import CollectionBatch, SourceVacancy


def parse_search(
    document: str,
    url: str,
    *,
    status_code: int = 200,
    limit: int = 5,
    observed_at: str | None = None,
) -> CollectionBatch:
    classified = classify_common(document, status_code)
    if classified:
        status = (
            "rate_limited"
            if status_code == 429
            else "blocked"
            if classified.status == "blocked"
            else "failed"
        )
        return CollectionBatch(status, url, http_status=status_code, error=classified.error)
    try:
        channel = ET.fromstring(document).find("channel")
        if channel is None or channel.find("title") is None:
            raise ValueError("Not a jobs RSS channel")
    except (ET.ParseError, ValueError):
        return CollectionBatch(
            "failed", url, http_status=status_code, error="Invalid jobs RSS; not an empty result"
        )
    jobs, invalid = {}, 0
    items = channel.findall("item")
    namespace = "{https://www.absolventen.at}"
    for item in items:
        target = item.findtext("link", "").strip()
        parsed = urlsplit(target)
        identity = re.fullmatch(r"/job/([^/]+)/?", parsed.path)
        title = html.unescape(item.findtext("title", "")).strip()
        if (
            not identity
            or parsed.hostname != "www.absolventen.at"
            or parsed.scheme != "https"
            or not title
        ):
            invalid += 1
            continue
        # The public feed exposes permalink GUIDs rather than WordPress database IDs.
        job = SourceVacancy(
            source="absolventen",
            source_job_id=identity[1],
            title=title,
            company=html.unescape(item.findtext(namespace + "company", "")),
            location=html.unescape(item.findtext(namespace + "location", "")),
            snippet=(
                BeautifulSoup(item.findtext("description", ""), "html.parser").get_text(
                    " ", strip=True
                )
                + "\nEmployment type: "
                + html.unescape(item.findtext(namespace + "job_type", ""))
            ).strip(),
            url=f"https://www.absolventen.at/job/{identity[1]}/",
            observed_at=observed_at or datetime.now(timezone.utc).isoformat(),
            published_at=item.findtext("pubDate", ""),
            connector="absolventen-rss-v1",
        )
        jobs.setdefault(job.dedupe_key, job)
    return CollectionBatch(
        "partial" if invalid and jobs else "failed" if invalid else "success" if jobs else "empty",
        url,
        jobs=tuple(jobs.values())[:limit],
        http_status=status_code,
        cards_seen=len(items),
        invalid_cards=invalid,
        error="Invalid RSS entries" if invalid else "",
        warnings=("RSS is a bounded recent window; absence is not expiry.",),
    )
