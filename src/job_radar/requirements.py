"""Conservative requirement extraction; absence of candidate facts stays review."""
import re


def education_requirement(row, profile):
    text = str(row.get("description") or row.get("snippet") or "")
    for sentence in re.split(r"[\n.!;]", text):
        if not re.search(r"\b(?:master(?:'s)? degree|masterabschluss|masterstudium)\b", sentence, re.I):
            continue
        if re.search(r"not required|not mandatory|nicht erforderlich|kein.{0,20}master|preferred|desirable|advantage|von vorteil|wünschenswert|or equivalent|oder gleichwertig|bachelor|enrolled|studying|laufend", sentence, re.I):
            continue
        if not re.search(r"required|must|completed|abgeschlossen|erforderlich|voraussetzung", sentence, re.I):
            continue
        facts = profile.get("verified_qualifications") or {}
        known = facts.get("confirmed") is True and isinstance(facts.get("completed_degrees"), list)
        satisfied = known and bool({str(x).lower() for x in facts["completed_degrees"]} & {"master", "doctorate", "phd"})
        if satisfied:
            return None
        return {"code": "education_mismatch" if known else "education_review",
                "detail": "Completed Master's degree required; candidate qualification " + ("incompatible" if known else "unknown"),
                "hard": known, "evidence": [sentence.strip()]}
    return None
