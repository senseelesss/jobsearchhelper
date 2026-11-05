from collections import defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from job_radar.availability import is_expired


def _received_at(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_direct(row: dict[str, Any]) -> bool:
    return not (
        str(row["source_job_id"]).startswith("sponsored-")
        or "/pagead/" in str(row["url"])
    )


def _canonical_rank(row: dict[str, Any]) -> tuple:
    return (
        not is_expired(row),
        row.get("fetch_status") == "success" and bool(row.get("description")),
        _is_direct(row),
        max(_received_at(str(row["email_received_at"])), _received_at(str(row.get("observed_at", "")))),
        len(str(row["snippet"])),
        str(row["dedupe_key"]),
    )


def semantic_groups(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row["semantic_key"])].append(row)
    return [
        sorted(group, key=_canonical_rank, reverse=True)
        for group in groups.values()
    ]


def collapse_semantic(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    collapsed: list[dict[str, Any]] = []
    for group in semantic_groups(rows):
        canonical = group[0]
        alternate_urls = list(
            dict.fromkeys(
                str(row["url"])
                for row in group
                if row is not canonical and row["url"] != canonical["url"]
            )
        )
        collapsed.append(
            {
                **canonical,
                "semantic_duplicate_count": len(group) - 1,
                "alternate_source_urls": alternate_urls,
            }
        )

    return sorted(
        collapsed,
        key=lambda row: (
            _received_at(str(row["email_received_at"])),
            str(row["dedupe_key"]),
        ),
        reverse=True,
    )
