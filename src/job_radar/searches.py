"""Validated, reusable public search URLs. Never accepts a login or detail URL."""

import re
import hashlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml


@dataclass(frozen=True)
class SavedSearch:
    name: str
    source: str
    url: str


def validate_search_url(url: str, name: str = "") -> SavedSearch:
    if len(url) > 4096:
        raise ValueError("Search URL is too long")
    parsed = urlsplit(url.strip())
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
    ):
        raise ValueError("Use a public HTTPS search URL without credentials or a custom port")
    host = parsed.hostname or ""
    if host in {"karriere.at", "www.karriere.at"}:
        source, host = "karriere", "www.karriere.at"
        if not parsed.path.startswith("/jobs") or re.fullmatch(r"/jobs/\d+/?", parsed.path):
            raise ValueError("Paste a karriere.at search URL, not a job detail URL")
        if parsed.path != "/jobs" and not parsed.path.startswith("/jobs/"):
            raise ValueError("Unsupported karriere.at search path")
    elif host in {"linkedin.com", "www.linkedin.com"} or re.fullmatch(
        r"[a-z]{2}\.linkedin\.com", host
    ):
        source, host = "linkedin", "www.linkedin.com"
        if parsed.path.rstrip("/") != "/jobs/search":
            raise ValueError("Paste a LinkedIn /jobs/search/ URL")
    else:
        raise ValueError("Supported search hosts: karriere.at and LinkedIn")
    pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in {"token", "access_token", "password", "session", "li_at"}:
            raise ValueError("Search URLs must not contain account tokens")
        if key.startswith("utm_") or key in {"trk", "trackingId", "refId", "currentJobId"}:
            continue
        pairs.append((key, value))
    clean = urlunsplit(("https", host, parsed.path, urlencode(pairs), ""))
    return SavedSearch(name or source, source, clean)


def load_searches(path: Path) -> list[SavedSearch]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("searches"), list):
        raise ValueError("Expected a YAML mapping with a searches list")
    searches = []
    names = set()
    urls = set()
    for item in data["searches"]:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            raise ValueError("Each search must have a URL")
        if "enabled" in item and not isinstance(item["enabled"], bool):
            raise ValueError("enabled must be a YAML boolean")
        if item.get("enabled", True) is False:
            continue
        name = item.get("name", "")
        if not isinstance(name, str) or len(name) > 80 or name in names and name:
            raise ValueError("Search names must be unique strings")
        search = validate_search_url(item["url"], name)
        if not name:
            search = SavedSearch(
                search.source + "-" + hashlib.sha256(search.url.encode()).hexdigest()[:8],
                search.source,
                search.url,
            )
        if search.url not in urls:
            searches.append(search)
            names.add(name)
            urls.add(search.url)
    if not searches or len(searches) > 8:
        raise ValueError("Configure between one and eight enabled searches")
    return searches
