"""Evidence-based hours classification; no technical score or legal assumptions."""

import re

WEEKLY = re.compile(
    r"\b(?P<low>\d{1,2}(?:[.,]\d+)?)\s*(?:(?:[-–—]|bis|to)\s*(?P<high>\d{1,2}(?:[.,]\d+)?))?\s*"
    r"(?:wochenstunden|stunden\s*(?:pro|/|je)\s*woche|hours?\s*(?:per|/|a)\s*week|(?:h|std\.?)\s*/\s*(?:woche|week|w))\b",
    re.I,
)
BARE_HOURS = re.compile(
    r"\b(?P<low>\d{1,2}(?:[.,]\d+)?)\s*(?:(?:[-–—]|bis|to)\s*(?P<high>\d{1,2}(?:[.,]\d+)?))?\s*(?:stunden|hours?|h|std\.?)\b",
    re.I,
)
PART = re.compile(r"\b(?:teilzeit|part[ _-]?time|parttime|geringfügig)\b", re.I)
FULL = re.compile(r"\b(?:vollzeit|full[ _-]?time|fulltime)\b", re.I)
FLEXIBLE = re.compile(
    r"(?:vollzeit\s*(?:oder|und|/|bzw\.?)\s*teilzeit|teilzeit\s*(?:oder|und|/)\s*vollzeit|full[ -]?time\s*(?:or|and|/)\s*part[ -]?time|part[ -]?time\s*(?:or|and|/)\s*full[ -]?time|part[ -]?time\s*(?:possible|available|option|flexibility)|teilzeit\s*(?:möglich|moeglich|optional))",
    re.I,
)
FULL_ONLY = re.compile(
    r"(?:\bnur\s+vollzeit\b|\bfull[ -]?time\s+only\b|\bonly\s+full[ -]?time\b|\bkeine\s+teilzeit\b|\bno\s+part[ -]?time\b|part[ -]?time\s+(?:is\s+)?not\s+(?:possible|available)|teilzeit\s+(?:ist\s+)?(?:nicht möglich|ausgeschlossen))",
    re.I,
)
PAY_BASIS = re.compile(
    r"(?:vollzeitbasis|full[ -]?time\s+(?:basis|equivalent)|basis\s+(?:of|von)|auf\s+basis|bei\s+(?:einer\s+)?vollzeit(?:beschäftigung)?|für\s+vollzeit|gehaltsbasis|salary\s+(?:based|reference)|referenzgehalt)",
    re.I,
)
GENERIC = re.compile(
    r"(?:our\s+(?:employees|company|team)|unsere\s+(?:mitarbeiter|beschäftigten|firma)|all\s+employees|benefits\s+for|for\s+example|zum\s+beispiel)",
    re.I,
)


def job_sections(row):
    """Prefer the current JD to an older truncated snippet; keep title separately."""
    body = str(row.get("description") or row.get("snippet") or "")
    return [("title", str(row.get("title") or ""))] + [
        ("jd" if row.get("description") else "snippet", line.strip())
        for line in re.split(r"\n|(?<=[.!?;])\s+(?=[A-ZÄÖÜ])", body)
        if line.strip()
    ]


def assess_hours(row, policy=None):
    policy = policy or {}
    target_low, target_high = policy.get("target_hours", [20, 25])
    accept_low, accept_high = policy.get("acceptable_hours", [15, 30])
    evidence, ranges = [], []
    part, flexible, full_only, likely_full = False, False, False, False

    def add(source, text):
        item = {"source": source, "text": text[:220]}
        if item not in evidence:
            evidence.append(item)

    for source, raw in job_sections(row) + [
        ("metadata", str(row.get("employment_type") or row.get("employmentType") or "")),
        ("metadata", str(row.get("work_hours") or row.get("workHours") or "")),
    ]:
        if not raw:
            continue
        if PAY_BASIS.search(raw) or GENERIC.search(raw):
            # Evidence retained, but employer/pay-reference hours aren't this role's hours.
            if WEEKLY.search(raw) or FULL.search(raw) or PART.search(raw):
                add(source + ":reference_only", raw)
            continue
        full_only_here = bool(FULL_ONLY.search(raw))
        flexible_here = bool(FLEXIBLE.search(raw)) or (
            bool(PART.search(raw) and FULL.search(raw))
            and (source == "metadata" or bool(re.search(r"employment\s*type", raw, re.I)))
        )
        part_here = bool(PART.search(raw)) and not full_only_here
        matches = list(WEEKLY.finditer(raw))
        # Austrian contract tags often say Vollzeit (38,5 Stunden), not /Woche.
        if not matches and (
            source in {"title", "metadata"}
            or FULL.search(raw)
            or re.search(r"arbeitszeit|work\s*hours", raw, re.I)
            or BARE_HOURS.fullmatch(raw.strip(" ."))
        ):
            if not re.search(r"(?:pro|/|je)\s*(?:tag|monat)|(?:per|/)\s*(?:day|month)", raw, re.I):
                matches = list(BARE_HOURS.finditer(raw))
        for match in matches:
            low = float(match["low"].replace(",", "."))
            high = float((match["high"] or match["low"]).replace(",", "."))
            if 0 < low <= high <= 80:
                ranges.append((low, high))
                add(source, raw)
        role_tag = source in {"title", "metadata"} or bool(
            re.search(
                r"^\s*(?:vollzeit|full[ _-]?time)\s*(?:[(/,;:]|$)|employment\s*type|anstellungsart|beschäftigungsart|arbeitszeit",
                raw,
                re.I,
            )
        )
        if part_here or flexible_here or full_only_here or FULL.search(raw):
            add(source, raw)
        part |= part_here
        flexible |= flexible_here
        full_only |= full_only_here or (
            bool(FULL.search(raw)) and role_tag and not part_here and not flexible_here
        )
        likely_full |= bool(FULL.search(raw)) and not part_here and not flexible_here
    if row.get("hours_min") is not None:
        try:
            low = float(row["hours_min"])
            high = float(row.get("hours_max") if row.get("hours_max") is not None else low)
            if 0 < low <= high <= 80:
                ranges.append((low, high))
                add("metadata", f"{low:g}–{high:g} hours/week")
        except (ValueError, TypeError):
            pass
    fits = [low <= accept_high and high >= accept_low for low, high in ranges]
    contradiction = (bool(fits) and any(fits) and not all(fits) and not flexible) or (
        full_only and (part or flexible or any(fits)) and not flexible
    )
    # An explicit "or part-time" is a real alternative to the full-time tag.
    if flexible:
        full_only = False
        # A full-time numeric reference with explicit alternative does not remove flexibility.
        if fits and not any(fits):
            fits = []
    if contradiction:
        fit = "unknown"
    elif full_only:
        fit = "confirmed_mismatch"
    elif ranges and fits:
        if any(low <= target_high and high >= target_low for low, high in ranges):
            fit = "confirmed_target"
        elif any(fits):
            fit = "acceptable"
        else:
            fit = "confirmed_mismatch"
    elif part or flexible:
        fit = "flexible_part_time"
    else:
        fit = "unknown"
    status = (
        "full_time_only"
        if fit == "confirmed_mismatch"
        else "part_time_available"
        if fit in {"confirmed_target", "acceptable", "flexible_part_time"}
        else "unknown"
    )
    substantive = [item for item in evidence if not item["source"].endswith(":reference_only")]
    return {
        "hours_fit": fit,
        "hours_status": status,
        "hours_target_fit": "confirmed"
        if fit == "confirmed_target"
        else "outside_target"
        if fit in {"acceptable", "confirmed_mismatch"}
        else "unknown",
        "hours_evidence": "; ".join(item["text"] for item in substantive)[:500] or "not stated",
        "hours_evidence_details": evidence,
        "hours_uncertain": bool(contradiction),
        "hours_mismatch_kind": ("full_time_only" if full_only else "outside_acceptable")
        if fit == "confirmed_mismatch"
        else "",
        "hours_likely_full_time": likely_full and fit == "unknown",
    }
