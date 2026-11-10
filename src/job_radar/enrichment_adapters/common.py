import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any


@dataclass(frozen=True, slots=True)
class ParsedPage:
    status: str
    description: str = ""
    description_language: str = ""
    description_confidence: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def completeness(self) -> str:
        return completeness(self.status, self.description, self.error)


class _ElementTextParser(HTMLParser):
    def __init__(self, attribute: str, values: set[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.attribute = attribute
        self.values = values
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(
        self, tag: str, attributes: list[tuple[str, str | None]]
    ) -> None:
        attrs = dict(attributes)
        if self.depth:
            if tag in {"br", "li", "p", "div", "h1", "h2", "h3"}:
                self.parts.append("\n")
            if tag not in {"br", "hr", "img", "input", "meta", "link"}:
                self.depth += 1
            return

        value = attrs.get(self.attribute, "") or ""
        candidates = set(value.split()) if self.attribute == "class" else {value}
        if candidates & self.values:
            self.depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self.depth:
            self.depth -= 1
            if tag in {"li", "p", "div", "h1", "h2", "h3"}:
                self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.parts.append(data)


def normalize_description(value: str) -> str:
    lines = [
        re.sub(r"\s+", " ", html.unescape(line)).strip()
        for line in value.splitlines()
    ]
    return "\n".join(line for line in lines if line)


def extract_element_text(
    document: str, attribute: str, values: set[str]
) -> str:
    parser = _ElementTextParser(attribute, values)
    parser.feed(document)
    return normalize_description("".join(parser.parts))


def _json_ld_objects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [obj for item in value for obj in _json_ld_objects(item)]
    if isinstance(value, dict):
        return [value, *_json_ld_objects(value.get("@graph", []))]
    return []


def job_postings(document: str) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    postings = []
    for script in BeautifulSoup(document, "html.parser").find_all("script"):
        if str(script.get("type", "")).lower().split(";")[0].strip() != "application/ld+json":
            continue
        try:
            value = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        for obj in _json_ld_objects(value):
            types = obj.get("@type", [])
            if "JobPosting" in (types if isinstance(types, list) else [types]):
                postings.append(obj)
    return postings


def structured_metadata(document: str) -> dict[str, Any]:
    postings = job_postings(document)
    if len(postings) != 1:
        return {}
    return {key: value for key, value in postings[0].items() if key in {
        "title", "hiringOrganization", "employmentType", "workHours", "jobLocation",
        "jobLocationType", "applicantLocationRequirements", "baseSalary", "datePosted",
        "validThrough", "url", "identifier", "responsibilities", "qualifications", "skills",
    }}


def extract_json_ld_description(document: str) -> str:
    from bs4 import BeautifulSoup

    postings = job_postings(document)
    # A search page containing multiple postings is never one complete vacancy.
    if len(postings) != 1 or not isinstance(postings[0].get("description"), str):
        return ""
    candidate = postings[0]
    description = normalize_description(BeautifulSoup(
        html.unescape(candidate["description"]), "html.parser"
    ).get_text("\n", strip=True))
    for key, label in (("employmentType", "Employment type"), ("workHours", "Work hours")):
        value = candidate.get(key)
        if isinstance(value, list):
            value = " / ".join(item for item in value if isinstance(item, str))
        if isinstance(value, str) and value.strip():
            description += f"\n{label}: {value.strip()[:500]}"
    if candidate.get("jobLocationType") == "TELECOMMUTE":
        requirements = candidate.get("applicantLocationRequirements", [])
        if isinstance(requirements, dict):
            requirements = [requirements]
        countries = [item.get("name", "") for item in requirements if isinstance(item, dict)]
        if any(country.casefold() in {"austria", "österreich", "at"} for country in countries):
            description += "\nRemote work from Austria (JobPosting applicantLocationRequirements)"
    return description


def completeness(status: str, description: str = "", error: str = "") -> str:
    if status == "expired" or error == "expired listing" or re.search(r"HTTP (?:404|410)\b", error):
        return "EXPIRED"
    if status == "blocked" or re.search(r"HTTP (?:401|403|429)|blocked page|Consent, login or empty template", error):
        return "INACCESSIBLE"
    if status == "success" and description:
        return "FULL"
    return "PARTIAL" if description else "MISSING"


def description_page(description: str, confidence: str,
                     metadata: dict[str, Any] | None = None) -> ParsedPage:
    """Container/structured-data evidence plus visible-content rejection, not size alone."""
    text = description.strip()
    if not text:
        return ParsedPage("empty", error="No job description found")
    if metadata and isinstance(metadata.get("validThrough"), str):
        from datetime import datetime, time, timezone
        try:
            deadline = datetime.fromisoformat(metadata["validThrough"].replace("Z", "+00:00"))
            if len(metadata["validThrough"]) == 10:
                deadline = datetime.combine(deadline.date(), time.max)
            deadline = deadline.replace(tzinfo=deadline.tzinfo or timezone.utc)
            if deadline < datetime.now(timezone.utc):
                return ParsedPage("expired", text, description_language(text), confidence,
                                  "expired listing", metadata)
        except ValueError:
            pass  # Invalid source metadata does not establish expiry.
    lowered = text.casefold()
    walls = ("accept all cookies", "cookie settings", "sign in to view", "login to continue",
             "enable javascript", "access denied", "verify you are human")
    if any(marker in lowered for marker in walls) and len(text.split()) < 100:
        return ParsedPage("blocked", error="Consent, login or empty template")
    truncated = text.endswith(("...", "…")) or any(x in lowered for x in (
        "sign in to read more", "log in to read more", "full description unavailable"))
    boilerplate = any(marker in lowered for marker in ("join our talent community", "subscribe to job alerts", "browse all our open positions"))
    if truncated or boilerplate:
        return ParsedPage("snippet_only", text, description_language(text), "low",
                          "Truncated or insufficient job evidence", metadata or {})
    return ParsedPage("success", text, description_language(text), confidence,
                      metadata=metadata or {})


def description_language(description: str) -> str:
    words = re.findall(r"\b[a-zäöüß]+\b", description.casefold())
    german_terms = {
        "und", "der", "die", "das", "den", "dem", "ein", "eine", "einer",
        "mit", "für", "von", "wir", "sie", "du", "deine", "unser", "auf",
        "als", "im", "in", "zu", "bei", "sowie", "kenntnisse",
    }
    english_terms = {
        "and", "the", "with", "for", "from", "we", "you", "your", "our",
        "a", "an", "of", "to", "in", "on", "as", "at", "or", "skills",
    }
    german = sum(word in german_terms for word in words)
    english = sum(word in english_terms for word in words)
    minimum_signal = max(2, len(words) // 50)
    if german >= minimum_signal and german >= english * 2:
        return "de"
    if english >= minimum_signal and english >= german * 2:
        return "en"
    return "unknown"


def description_hash(description: str) -> str:
    return hashlib.sha256(description.encode()).hexdigest()


def classify_common(document: str, status_code: int) -> ParsedPage | None:
    lowered_document = document.casefold()
    lowered = normalize_description(re.sub(r"<[^>]+>", " ", document)).casefold()
    if status_code in {404, 410} or any(
        marker in lowered
        for marker in (
            "this job is no longer available",
            "job has expired",
            "no longer accepting applications",
            "stelle ist nicht mehr verfügbar",
            "stellenanzeige ist abgelaufen",
        )
    ):
        return ParsedPage(
            "expired",
            error=f"HTTP {status_code}" if status_code >= 400 else "expired listing",
        )
    if status_code in {401, 403, 429} or any(
        marker in lowered
        for marker in (
            "captcha",
            "verify you are human",
            "security verification",
            "unusual traffic",
            "sign in to view",
            "login to continue",
        )
    ) or any(
        marker in lowered_document
        for marker in (
            "authwall",
            "login__form",
            "/checkpoint/challenge",
            "data-test-id=\"sign-in-form\"",
            "id=\"loginform\"",
        )
    ):
        return ParsedPage(
            "blocked",
            error=f"HTTP {status_code}" if status_code >= 400 else "blocked page",
        )
    if status_code >= 400:
        return ParsedPage("failed", error=f"HTTP {status_code}")
    return None
