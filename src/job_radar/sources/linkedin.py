import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from job_radar.sources.base import CollectionBatch, PageClient, SourceVacancy

BASE_URL = "https://www.linkedin.com"
CONNECTOR_VERSION = "linkedin-public-search-v1"


def search_url(query: str, location: str) -> str:
    query, location = query.strip(), location.strip()
    if not query or not location or max(len(query), len(location)) > 120:
        raise ValueError("Query and location must contain 1–120 characters")
    return f"{BASE_URL}/jobs/search/?{urlencode({'keywords': query, 'location': location})}"


def _linkedin_host(host: str | None) -> bool:
    return host == "linkedin.com" or bool(host and host.endswith(".linkedin.com"))


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
        "verify you are human", "security verification", "access denied",
        "unusual traffic", "complete the captcha",
    )) or soup.select_one('form[action*="checkpoint/challenge"]'):
        return CollectionBatch(
            "blocked", url, http_status=200, error="Challenge or access wall"
        )

    count_node = soup.select_one(".results-context-header__job-count")
    count_text = count_node.get_text(" ", strip=True) if count_node else ""
    count = re.fullmatch(r"([\d.,\s]+)(\+)?", count_text)
    digits = re.sub(r"\D", "", count.group(1)) if count else ""
    total = int(digits) if digits else None
    lower_bound = bool(count and count.group(2))
    cards = soup.select(
        ".base-search-card, .job-search-card, "
        '.base-card[data-entity-urn^="urn:li:jobPosting:"]'
    )
    jobs: dict[str, SourceVacancy] = {}
    invalid = 0
    timestamp = observed_at or datetime.now(timezone.utc).isoformat()
    for card in cards:
        title_node = card.select_one(".base-search-card__title")
        link = card.select_one("a.base-card__full-link")
        target = urljoin(BASE_URL, str(link.get("href", ""))) if link else ""
        parsed = urlsplit(target)
        identity = re.fullmatch(r"/jobs/view/(?:[^/]*-)?(\d+)/?", parsed.path)
        urn = str(card.get("data-entity-urn", ""))
        urn_identity = re.fullmatch(r"urn:li:jobPosting:(\d+)", urn)
        title = title_node.get_text(" ", strip=True) if title_node else ""
        if (
            not identity or not _linkedin_host(parsed.hostname)
            or parsed.scheme != "https" or not title
            or (urn and (not urn_identity or urn_identity.group(1) != identity.group(1)))
        ):
            invalid += 1
            continue
        source_id = identity.group(1)
        company = card.select_one(".base-search-card__subtitle")
        location = card.select_one(".job-search-card__location")
        time = card.select_one("time[datetime]")
        published = str(time.get("datetime", "")) if time else ""
        try:
            datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            published = ""
        vacancy = SourceVacancy(
            source="linkedin", source_job_id=source_id, title=title,
            company=company.get_text(" ", strip=True) if company else "",
            location=location.get_text(" ", strip=True) if location else "",
            snippet="", url=f"{BASE_URL}/jobs/view/{source_id}/",
            observed_at=timestamp, connector=CONNECTOR_VERSION,
            description_kind="missing", published_at=published,
        )
        jobs.setdefault(vacancy.dedupe_key, vacancy)

    if not jobs:
        login_wall = soup.select_one(".authwall, .login__form, #loginform")
        if login_wall:
            return CollectionBatch("blocked", url, http_status=200, error="Login required")
        empty_banner = soup.select_one(".jobs-search-no-results-banner")
        empty = not cards and (
            total == 0 or bool(empty_banner and any(
                marker in empty_banner.get_text(" ", strip=True).casefold()
                for marker in ("no matching jobs", "no results", "keine ergebnisse")
            ))
        )
        return CollectionBatch(
            "empty" if empty else "failed", url, http_status=200,
            cards_seen=len(cards), invalid_cards=invalid, reported_total=total,
            reported_total_is_lower_bound=lower_bound,
            error="" if empty else "No valid job cards; unknown or changed layout",
        )
    return CollectionBatch(
        "partial" if invalid else "success", url, jobs=tuple(jobs.values())[:limit],
        http_status=200, cards_seen=len(cards), invalid_cards=invalid,
        reported_total=total, reported_total_is_lower_bound=lower_bound,
        warnings=(
            "First page only; pagination is not implemented.",
            "Public search cards contain no full descriptions; detail fetching is pending.",
        ),
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
        return CollectionBatch(
            "failed", url, error=f"Transport failure: {type(error).__name__}"
        )
    final = urlsplit(response.final_url)
    if (
        final.scheme != "https" or not _linkedin_host(final.hostname)
        or final.path.rstrip("/") != "/jobs/search"
    ):
        return CollectionBatch(
            "blocked", url, http_status=response.status_code,
            error="Unexpected redirect away from public LinkedIn job search",
        )
    return parse_search(
        response.body, url, status_code=response.status_code, limit=limit
    )
