import re
from datetime import datetime, timezone
from urllib.parse import quote, urljoin, urlsplit

from bs4 import BeautifulSoup

from job_radar.sources.base import CollectionBatch, PageClient, SourceVacancy

BASE_URL = "https://www.karriere.at"
CONNECTOR_VERSION = "karriere-search-v1"


def search_url(query: str, location: str) -> str:
    def slug(value: str) -> str:
        value = re.sub(r"\s+", "-", value.strip().lower())
        if not value or len(value) > 120 or value in {".", ".."}:
            raise ValueError("Query and location must contain 1–120 characters")
        return quote(value, safe="")

    return f"{BASE_URL}/jobs/{slug(query)}/{slug(location)}"


def parse_search(
    document: str,
    url: str,
    *,
    status_code: int = 200,
    limit: int = 5,
    observed_at: str | None = None,
) -> CollectionBatch:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if status_code == 429:
        return CollectionBatch("rate_limited", url, http_status=429, error="HTTP 429")
    if status_code in {401, 403, 999}:
        return CollectionBatch(
            "blocked", url, http_status=status_code, error=f"HTTP {status_code}"
        )
    if status_code != 200:
        return CollectionBatch(
            "failed", url, http_status=status_code, error=f"HTTP {status_code}"
        )

    soup = BeautifulSoup(document, "html.parser")
    visible = soup.get_text(" ", strip=True).casefold()
    if any(marker in visible for marker in (
        "verify you are human", "access denied", "security verification",
        "unusual traffic", "complete the captcha",
    )):
        return CollectionBatch(
            "blocked", url, http_status=200, error="Challenge or access wall"
        )

    heading = soup.select_one("h1")
    count = re.match(r"^([\d.]+)\b.*\bJobs\b", heading.get_text(" ", strip=True)) if heading else None
    total = int(count.group(1).replace(".", "")) if count else None
    cards = soup.select(".m-jobsListItem")
    jobs: dict[str, SourceVacancy] = {}
    invalid = 0
    timestamp = observed_at or datetime.now(timezone.utc).isoformat()

    for card in cards:
        link = card.select_one(".m-jobsListItem__titleLink")
        company = card.select_one(".m-jobsListItem__companyName")
        target = urljoin(BASE_URL, str(link.get("href", ""))) if link else ""
        parsed = urlsplit(target)
        identity = re.fullmatch(r"/jobs/(\d+)/?", parsed.path)
        title = link.get_text(" ", strip=True) if link else ""
        if (
            not identity or parsed.hostname != "www.karriere.at"
            or parsed.scheme != "https" or not title
        ):
            invalid += 1
            continue
        source_id = identity.group(1)
        snippet = card.select_one(".m-jobListSummary__text")
        locations = [item.get_text(" ", strip=True).strip(" ,") for item in card.select(".m-jobsListItem__location")]
        vacancy = SourceVacancy(
            source="karriere", source_job_id=source_id,
            title=title, company=company.get_text(" ", strip=True) if company else "",
            location=", ".join(dict.fromkeys(filter(None, locations))),
            snippet=snippet.get_text(" ", strip=True) if snippet else "",
            url=f"{BASE_URL}/jobs/{source_id}", observed_at=timestamp,
            connector=CONNECTOR_VERSION,
        )
        jobs.setdefault(vacancy.dedupe_key, vacancy)

    if not jobs:
        empty = not cards and total == 0
        return CollectionBatch(
            "empty" if empty else "failed", url,
            http_status=200, cards_seen=len(cards), invalid_cards=invalid,
            reported_total=total,
            error="" if empty else "No valid job cards; unknown or changed layout",
        )
    return CollectionBatch(
        "partial" if invalid else "success", url,
        jobs=tuple(jobs.values())[:limit], http_status=200,
        cards_seen=len(cards), invalid_cards=invalid, reported_total=total,
    )


def collect(
    client: PageClient, query: str, location: str, limit: int = 5
) -> CollectionBatch:
    url = search_url(query, location)
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    try:
        response = client.get(url)
    except Exception as error:
        # Browser/HTTP errors may contain URLs or session details. Export the type only.
        return CollectionBatch(
            "failed", url, error=f"Transport failure: {type(error).__name__}"
        )
    final = urlsplit(response.final_url)
    if final.hostname != "www.karriere.at" or not final.path.startswith("/jobs/"):
        return CollectionBatch(
            "blocked", url, http_status=response.status_code,
            error="Unexpected redirect away from public job search",
        )
    return parse_search(
        response.body, url, status_code=response.status_code, limit=limit
    )
