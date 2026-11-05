"""Conservative evidence about hours, independent of technical scoring."""

import re


def assess_hours(row):
    text = " ".join(str(row.get(key) or "") for key in ("title", "snippet", "description"))
    part = re.search(r"\b(?:teilzeit|part[ -]?time|geringfügig)\b", text, re.I)
    full = re.search(r"\b(?:vollzeit|full[ -]?time)\b", text, re.I)
    weekly = re.search(
        r"\b(\d{1,2}(?:[.,]\d+)?)\s*(?:[-–]\s*(\d{1,2}(?:[.,]\d+)?))?\s*"
        r"(?:wochenstunden|stunden\s*(?:pro|/|je)\s*woche|hours?\s*(?:per|/)\s*week|h\s*/\s*w)\b",
        text,
        re.I,
    )
    if part:
        return {"hours_status": "part_time_available", "hours_evidence": part.group(0)}
    if weekly:
        low = float(weekly[1].replace(",", "."))
        high = float((weekly[2] or weekly[1]).replace(",", "."))
        if 0 < low <= high <= 60:
            if high <= 30:
                return {"hours_status": "part_time_available", "hours_evidence": weekly[0]}
            if low <= 30:
                return {"hours_status": "unknown", "hours_evidence": weekly[0]}
    # A generic benefits paragraph mentioning full-time is insufficient to exclude a role.
    title_full = re.search(r"\b(?:vollzeit|full[ -]?time)\b", str(row.get("title", "")), re.I)
    explicit_only = re.search(r"\b(?:nur\s+vollzeit|full[ -]?time\s+only)\b", text, re.I)
    if title_full or explicit_only:
        return {
            "hours_status": "full_time_only",
            "hours_evidence": (title_full or explicit_only)[0],
        }
    return {"hours_status": "unknown", "hours_evidence": full[0] if full else "not stated"}
