import hashlib
import html
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any


@dataclass(frozen=True, slots=True)
class ParsedPage:
    status: str
    description: str = ""
    description_language: str = ""
    description_confidence: str = ""
    error: str = ""


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
    if isinstance(value, dict):
        objects = [value]
        graph = value.get("@graph")
        if isinstance(graph, list):
            objects.extend(item for item in graph if isinstance(item, dict))
        return objects
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def extract_json_ld_description(document: str) -> str:
    scripts = re.findall(
        r"<script\b[^>]*type\s*=\s*([\"'])application/ld\+json\1[^>]*>"
        r"([\s\S]*?)</script>",
        document,
        re.IGNORECASE,
    )
    for _, payload in scripts:
        try:
            value: Any = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for candidate in _json_ld_objects(value):
            types = candidate.get("@type")
            type_values = types if isinstance(types, list) else [types]
            if "JobPosting" in type_values and candidate.get("description"):
                return normalize_description(
                    re.sub(r"<[^>]+>", "\n", str(candidate["description"]))
                )
    return ""


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
