"""Resolve only explicit public Greenhouse/Lever posting links; never guess boards."""

import html
import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from job_radar.enrichment_adapters.common import (
    ParsedPage,
    classify_common,
    description_page,
    normalize_description,
)


def public_posting(url: str) -> tuple[str, str] | None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
    ):
        return None
    if parsed.hostname in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        match = re.fullmatch(r"/([a-zA-Z0-9_-]+)/jobs/(\d+)/?", parsed.path)
        if match:
            board, identity = match.groups()
            return (
                "greenhouse",
                f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{identity}",
            )
    if parsed.hostname in {"jobs.lever.co", "jobs.eu.lever.co"}:
        match = re.fullmatch(r"/([a-zA-Z0-9_-]+)/([a-fA-F0-9-]{36})/?", parsed.path)
        if match:
            board, identity = match.groups()
            api = "api.eu.lever.co" if parsed.hostname == "jobs.eu.lever.co" else "api.lever.co"
            return "lever", f"https://{api}/v0/postings/{board}/{identity}?mode=json"
    return None


def application_link(document: str) -> str:
    soup = BeautifulSoup(document, "html.parser")
    # Multiple distinct postings are ambiguous. Never crawl a company's job board.
    links = {
        str(a["href"]).split("#")[0]
        for a in soup.select("a[href]")
        if public_posting(str(a["href"]))
    }
    return next(iter(links)) if len(links) == 1 else ""


def parse_posting(document: str, status_code: int, source: str) -> ParsedPage:
    if status_code != 200:
        return classify_common("", status_code) or ParsedPage("failed", error=f"HTTP {status_code}")
    try:
        value = json.loads(document)
        if not isinstance(value, dict):
            raise ValueError()
        metadata = {
            "extraction": source + "_public_api",
            "title": value.get("title", value.get("text", "")),
        }
        if source == "greenhouse":
            body = value.get("content", "")
        else:
            body = (
                value.get("description")
                or value.get("descriptionBody")
                or value.get("descriptionPlain")
                or ""
            )
            for section in value.get("lists", []):
                body += f"\n{section.get('text', '')}\n{section.get('content', '')}"
            supplement = value.get("salaryDescription") or value.get("salaryDescriptionPlain") or ""
            # Some employers put contract conditions and duties in this documented
            # field. Keep the evidence; never derive hours from the numeric pay range.
            if supplement and supplement not in body:
                body += "\n" + supplement
            body += "\n" + (value.get("additional") or value.get("additionalPlain") or "")
            if not BeautifulSoup(html.unescape(body), "html.parser").get_text(strip=True):
                return ParsedPage("empty", error="No public posting description")
            workplace = value.get("workplaceType")
            if workplace in {"hybrid", "remote", "on-site"}:
                body += "\nWorkplace: " + workplace
            metadata.update(
                {
                    k: value[k]
                    for k in ("workplaceType", "salaryRange", "categories", "applyUrl")
                    if value.get(k)
                }
            )
            commitment = value.get("categories", {}).get("commitment", "")
            if commitment:
                body += "\nEmployment type: " + str(commitment)
        text = normalize_description(
            BeautifulSoup(html.unescape(body), "html.parser").get_text("\n", strip=True)
        )
        return description_page(
            text,
            "high",
            metadata,
        )
    except (ValueError, TypeError, AttributeError):
        return ParsedPage("failed", error="Malformed public ATS response")
