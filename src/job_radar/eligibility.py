"""Explainable application constraints and priority, separate from cached AI fit."""

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re

from job_radar.availability import is_expired
from job_radar.work_hours import assess_hours, job_sections

DEFAULT_POLICY = Path(__file__).with_name("eligibility_rules.v1.json")
PRIORITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "EXCLUDED": 3}


@lru_cache(maxsize=8)
def _read_policy(path, stamp):
    raw = Path(path).read_bytes()
    value = json.loads(raw)
    for key in ("target_hours", "acceptable_hours"):
        if len(value[key]) != 2 or not 0 < value[key][0] <= value[key][1] <= 80:
            raise ValueError("Invalid eligibility hour bounds")
    if (
        not value["acceptable_hours"][0]
        <= value["target_hours"][0]
        <= value["target_hours"][1]
        <= value["acceptable_hours"][1]
    ):
        raise ValueError("Target hours must be within acceptable hours")
    value["policy_id"] = value["version"] + "-" + hashlib.sha256(raw).hexdigest()[:10]
    return value


def load_policy(path=None):
    path = Path(path or os.getenv("JOB_RADAR_ELIGIBILITY_RULES", DEFAULT_POLICY)).resolve()
    return _read_policy(str(path), path.stat().st_mtime_ns)


def _matches(text, terms):
    found = []
    for term in terms:
        # Avoid Java matching JavaScript and C# matching an arbitrary C.
        pattern = re.escape(term).replace(r"\ ", r"[\s_-]+")
        if term == "node.js":
            pattern = r"node\.?js"
        elif term == "postgresql":
            pattern = r"postgres(?:ql)?"
        elif term.endswith(("api", "integration")):
            pattern += "s?"
        if re.search(r"(?<![\w])" + pattern + r"(?![\w])", text, re.I):
            found.append(term)
    return found


def assess_seniority(row, policy=None):
    policy = policy or load_policy()
    title = str(row.get("title") or "")
    sections = job_sections(row)
    evidence, required, preferred = [], [], []
    junior = bool(
        re.search(r"\b(?:junior|jr\.?|entry[ -]?level|graduate|berufseinsteiger)\b", title, re.I)
    )
    student = bool(
        re.search(
            r"\b(?:werkstudent\w*|working student|student\w*|studienbegleitend)\b", title, re.I
        )
    )
    intern = bool(re.search(r"\b(?:intern(?:ship)?|praktik\w*|trainee)\b", title, re.I))
    level, fit = (
        ("student", "student")
        if student
        else ("internship", "internship")
        if intern
        else ("junior", "junior")
        if junior
        else ("unspecified", "unspecified")
    )
    title_levels = [
        (
            "manager",
            r"\b(?:(?:engineering|it domain|it|project|product|program|team|delivery|development|department)[ -]+manager|head\b|director\b|teamleiter\w*|abteilungsleiter\w*|personalverantwortung)",
        ),
        ("architect", r"\b(?:architect|architekt)\b"),
        ("lead", r"\b(?:lead|principal|staff(?: software)? engineer|technical lead)\b"),
        ("senior", r"\b(?:senior|sr\.?)\b"),
    ]
    for kind, pattern in title_levels:
        if re.search(pattern, title, re.I):
            if (
                kind == "manager"
                and (student or intern)
                and not re.search(r"head|director", title, re.I)
            ):
                level, fit = ("student", "student") if student else ("internship", "internship")
            elif kind == "senior" and (junior or student or intern):
                level, fit = "unspecified", "uncertain"
            else:
                level, fit = kind, "mismatch"
            evidence.append(title[:220])
            break
    years_pattern = re.compile(
        r"\b(\d{1,2})\s*(?:(?:[-–]|bis|to)\s*(\d{1,2}))?\s*\+?\s*(?:years?|jahre[n]?|jährige[nr]?|jaehrige[nr]?)\b",
        re.I,
    )
    for _, raw in sections:
        text = raw
        for word, number in [("four", "4"), ("five", "5"), ("vier", "4"), ("fünf", "5")]:
            text = re.sub(r"\b" + word + r"\b", number, text, flags=re.I)
        if re.search(
            r"\b(?:you will|du wirst|sie übernehmen)\b.{0,35}\b(?:manage|lead|leiten)\s+(?:(?:a|the|our|an|engineering|software|backend|development|ein|das|unser)\s+){0,3}(?:team|direct reports|mitarbeiter)",
            text,
            re.I,
        ):
            level, fit = "manager", "mismatch"
            evidence.append(raw[:220])
        for match in years_pattern.finditer(text):
            around = text[max(0, match.start() - 80) : match.end() + 100]
            if not re.search(r"experience|erfahrung|berufspraxis", around, re.I):
                continue
            if re.search(
                r"(?:of|an)\s+(?:data|history|logs)|company|unternehmen|founded|gegründet|combined experience|collectively",
                around,
                re.I,
            ):
                if not re.search(
                    r"required|must|minimum|mindestens|you have|du hast|sie haben", around, re.I
                ):
                    continue
            clause = (
                re.split(r"[,;]", text[: match.start()])[-1][-80:]
                + match[0]
                + re.split(r"[,;]", text[match.end() :])[0][:100]
            )
            soft = bool(
                re.search(
                    r"preferred|preferably|desirable|ideally|optional|nice.to.have|wünschenswert|von vorteil|idealerweise|a plus|not required|nicht erforderlich|up to|bis zu|less than",
                    clause,
                    re.I,
                )
            )
            (preferred if soft else required).append(int(match[1]))
            evidence.append(raw[:220])
    minimum = max(required, default=0)
    if minimum >= policy["excluded_required_years"]:
        level, fit = "senior", "mismatch"
    elif minimum >= policy["possible_required_years"] and fit != "mismatch":
        level, fit = "mid", "possible"
    elif required and fit == "unspecified":
        fit = "compatible"
    return {
        "seniority_level": level,
        "seniority_fit": fit,
        "required_years": minimum if required else None,
        "preferred_years": max(preferred, default=0) or None,
        "seniority_evidence": list(dict.fromkeys(evidence)),
    }


def assess_location(row, profile=None):
    profile = profile or {}
    location = str(row.get("location") or "")
    body = "\n".join(text for _, text in job_sections(row))
    evidence = location[:180] or "Location not stated"
    remote = bool(re.search(r"\bremote\b|fully remote|100.?% remote", location + " " + body, re.I))
    if re.search(
        r"no remote|remote\s+(?:work\s+)?(?:is\s+)?not\s+(?:possible|available|allowed)|on[ -]?site only|nur vor ort|kein(?:e)?\s+(?:remote|homeoffice)",
        body,
        re.I,
    ):
        remote = False
    # A role that restricts the actual work country cannot be assumed Austria-remote.
    restricted = re.search(
        r"(?:must (?:be (?:based|located)|reside)|residents? only|only (?:in|from)|ausschließlich|nur aus)\s+(?:in\s+)?(?:the\s+)?(US|USA|United States|UK|United Kingdom|Germany|Deutschland|Switzerland|Schweiz)\b",
        body,
        re.I,
    )
    restricted = restricted or re.search(
        r"\b(?:US|USA|United States|UK|Germany|Deutschland|Switzerland|Schweiz)\s+only\b",
        location,
        re.I,
    )
    citizen = re.search(
        r"(US|U\.S\.|UK|British|German|Schweizer)\s+(?:citizenship|citizens?|Staatsbürgerschaft)\s+(?:required|only|erforderlich)",
        body,
        re.I,
    )
    if restricted:
        return {"location_fit": "mismatch", "location_evidence": restricted[0][:180]}
    if citizen:
        authorization = profile.get("work_authorization") or {}
        known = (
            isinstance(authorization, dict)
            and authorization.get("verified") is True
            and isinstance(authorization.get("citizenships"), list)
        )
        aliases = {
            "us": "US",
            "u.s.": "US",
            "uk": "GB",
            "british": "GB",
            "german": "DE",
            "schweizer": "CH",
        }
        required = aliases.get(citizen[1].casefold(), citizen[1].upper())
        if not known:
            return {
                "location_fit": "authorization_unverified",
                "location_evidence": citizen[0][:180],
            }
        citizenships = {
            aliases.get(str(item).casefold(), str(item).upper())
            for item in authorization["citizenships"]
        }
        if required not in citizenships:
            return {"location_fit": "mismatch", "location_evidence": citizen[0][:180]}
    if re.search(r"\b(?:vienna|wien)\b", location, re.I):
        fit = "vienna"
    elif remote and re.search(r"\b(?:austria|österreich|osterreich)\b", location, re.I):
        fit = "austria_remote"
    elif remote and re.search(
        r"(?:work\s+(?:remotely\s+)?from|remote\s+(?:work\s+)?(?:from|in))\s+(?:anywhere in\s+)?(?:austria|österreich)|aus\s+österreich\s+arbeiten",
        body,
        re.I,
    ):
        fit = "austria_remote"
    elif remote and re.search(
        r"\b(?:EU|Europe|European Union|Europa|EMEA|DACH)\b", location + " " + body, re.I
    ):
        authorization = profile.get("work_authorization") or {}
        # Explicit verified profile facts are required, never inferred from residence.
        verified = (
            isinstance(authorization, dict)
            and authorization.get("verified") is True
            and authorization.get("eu_remote_from_austria") is True
        )
        fit = "eu_remote_verified" if verified else "eu_remote_unverified"
    elif remote:
        fit = "remote_unverified"
    elif location.strip():
        fit = "mismatch"
    else:
        fit = "unknown"
    return {"location_fit": fit, "location_evidence": evidence}


def assess_role(row, profile=None, policy=None):
    policy = policy or load_policy()
    title = str(row.get("title") or "")
    text = "\n".join(value for _, value in job_sections(row))
    excluded = re.search(
        r"\b(?:account executive|sales (?:representative|manager|engineer)|business development|marketing|communications?|human resources|hr generalist|accountant|accounting|buchhalter\w*|treasury|recruiter|customer success|product manager|project manager|program manager|scrum master|agile coach)\b",
        title,
        re.I,
    )
    technical_marketing = re.search(
        r"marketing technology|marketing automation|martech", title, re.I
    )
    if excluded and not technical_marketing:
        return {"role_fit": "unrelated", "role_evidence": [excluded[0]], "technical_terms": []}
    # Occupational context wins over incidental technology in non-IT adverts.
    clinical = re.search(
        r"\b(?:dental[ -]assistant|dent(?:al|ist)|zahn(?:arzt|ärzt|arztassist|ärztlich)\w*|zahnarztassistenz|ordination(?:sassisten\w*)?|pflege(?:assist\w*|fach\w*)|rezeptionist\w*|office assistant|büroassist\w*|medical assistant|medizinische assist\w*)\b",
        title, re.I,
    )
    technical_title = re.search(
        r"\b(?:software|developer|entwickler\w*|engineer|informatik\w*|IT|data|QA|automation|automatisierung|Odoo)\b", title, re.I,
    )
    if clinical and not technical_title:
        return {"role_fit": "unrelated", "role_evidence": [clinical[0]], "technical_terms": []}
    strong = _matches(text, policy["strong_technologies"])
    secondary = _matches(text, policy["secondary_technologies"])
    other = _matches(text, policy["other_technologies"])
    other_domain = _matches(title, policy.get("other_domains", []))
    development = bool(
        re.search(
            r"\b(?:software|backend|developer|development|entwickler\w*|softwareentwick\w*|qa|testautomatisierung|data engineer|data analyst|datenanal\w*|integrations? engineer|automation engineer)\b",
            title,
            re.I,
        )
    )
    development = development or bool(re.search(
        r"\b(?:AI(?:\s*/\s*ML)?|ML|machine learning|DevOps|data platform)\s+(?:engineer|developer|entwickler\w*)\b",
        title, re.I,
    )) or bool(re.search(r"\b(?:test|quality assurance) engineer\b", title, re.I)
               and re.search(r"\b(?:ERP|software|API|Selenium|SQL|automation|automatisierung)\b", text, re.I))
    adjacent = bool(
        re.search(
            r"\b(?:support|technical operations|agile delivery|it analyst|business analyst|application manager|app manager)\b",
            title,
            re.I,
        )
    )
    if adjacent and not development:
        fit = "adjacent"
    elif other_domain and not set(strong) - {"sql", "automation", "integration"}:
        fit = "other_stack"
    elif other and not set(strong) - {"sql", "automation", "integration"} and development:
        fit = "other_stack"
    elif strong and (
        development
        or adjacent
        or re.search(
            r"\b(?:python|typescript|javascript|automation|automatisierung|integration|llm)\b",
            title,
            re.I,
        )
    ):
        fit = "strong"
    elif secondary and development:
        fit = "secondary"
    elif other and development:
        fit = "other_stack"
    elif development:
        fit = "relevant"
    elif adjacent:
        fit = "adjacent"
    else:
        fit = "unknown"
    return {
        "role_fit": fit,
        "role_evidence": (strong + secondary + other)[:12] or [title[:150]],
        "technical_terms": strong + secondary,
    }


def _stamp(value):
    try:
        date = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp()
    except ValueError:
        return 0.0


def assess_application(row, profile=None, policy=None, now=None):
    policy = policy or load_policy()
    profile = profile or {}
    # Preferences may be locally overridden without adding facts to the verified CV.
    overrides = profile.get("search_preferences") or {}
    if overrides:
        policy = {
            **policy,
            **{k: overrides[k] for k in ("target_hours", "acceptable_hours") if k in overrides},
        }
    now = now or datetime.now(timezone.utc)
    hours = assess_hours(row, policy)
    senior = assess_seniority(row, policy)
    location = assess_location(row, profile)
    role = assess_role(row, profile, policy)
    reasons = []

    def reason(code, text, hard=False, evidence=None):
        reasons.append({"code": code, "detail": text, "hard": hard, "evidence": evidence or []})

    if hours["hours_fit"] == "confirmed_mismatch":
        reason(
            "hours_mismatch",
            "Confirmed hours outside acceptable range/full-time only",
            True,
            hours["hours_evidence_details"],
        )
    elif hours["hours_fit"] == "unknown":
        reason(
            "hours_uncertain" if hours["hours_uncertain"] else "hours_unknown",
            "Contradictory hours; verify with employer"
            if hours["hours_uncertain"]
            else "Hours need confirmation",
            evidence=hours["hours_evidence_details"],
        )
    if senior["seniority_fit"] == "mismatch":
        reason(
            "seniority_mismatch",
            "Seniority/required professional experience is incompatible",
            True,
            senior["seniority_evidence"],
        )
    elif senior["seniority_fit"] in {"unspecified", "uncertain", "possible"}:
        reason(
            "seniority_review",
            "Seniority or experience needs human review",
            evidence=senior["seniority_evidence"],
        )
    if location["location_fit"] == "mismatch":
        reason(
            "location_mismatch",
            "Location or explicit work-country requirement is incompatible",
            True,
            [location["location_evidence"]],
        )
    elif location["location_fit"] in {
        "unknown",
        "remote_unverified",
        "eu_remote_unverified",
        "authorization_unverified",
    }:
        reason(
            "location_review",
            "Verify ability to work remotely from Austria; eligibility not assumed",
            evidence=[location["location_evidence"]],
        )
    if role["role_fit"] == "unrelated":
        reason(
            "role_mismatch",
            "Primarily sales/management/nontechnical role",
            True,
            role["role_evidence"],
        )
    elif role["role_fit"] in {"unknown", "adjacent", "other_stack"}:
        reason(
            "role_review",
            "Adjacent role or weak/unknown relevant stack",
            evidence=role["role_evidence"],
        )
    if row.get("screening_status") == "rejected":
        reason("screening_rejected", "Hard deterministic screening rejection", True)
    if is_expired(row):
        reason("expired", "Listing is expired", True)
    if row.get("application_state") not in {None, "", "not_applied", "saved"}:
        reason("already_actioned", "Application already actioned; do not recommend again", True)
    full = bool(
        row.get("description")
        and row.get("fetch_status") == "success"
        and row.get("description_confidence") in {"medium", "high"}
        and not str(row.get("fetch_error", "")).startswith("Refresh failed")
    )
    evidence_quality = (
        "conflicting"
        if hours["hours_uncertain"] or senior["seniority_fit"] == "uncertain"
        else "full_jd"
        if full
        else "snippet"
        if row.get("snippet") or row.get("description")
        else "title_only"
    )
    score = row.get("match_score")
    if row.get("recommendation") == "skip":
        reason("ai_review", "Stored AI recommendation is skip; manual review only")
    if isinstance(score, (int, float)) and score < policy["weak_technical_threshold"]:
        reason("technical_review", "Technical fit below preferred threshold; manual review")
    points = (
        policy["hours_points"][hours["hours_fit"]]
        + policy["seniority_points"][senior["seniority_fit"]]
        + policy["role_points"][role["role_fit"]]
        + policy["location_points"][location["location_fit"]]
    )
    points += 3 if full else 0
    age = (now.timestamp() - _stamp(row.get("published_at"))) / 86400
    points += 2 if 0 <= age <= policy["freshness_days"] else 0
    points += min(10, max(0, score / 10)) if isinstance(score, (int, float)) else 0
    hard = any(item["hard"] for item in reasons)
    priority = (
        "EXCLUDED"
        if hard
        else "HIGH"
        if points >= policy["priority_thresholds"]["HIGH"]
        else "MEDIUM"
        if points >= policy["priority_thresholds"]["MEDIUM"]
        else "LOW"
    )
    if priority == "HIGH" and (
        hours["hours_fit"] == "unknown"
        or senior["seniority_fit"] not in {"student", "junior", "internship", "compatible"}
        or location["location_fit"] not in {"vienna", "austria_remote", "eu_remote_verified"}
    ):
        priority = "MEDIUM"
    if not hard and (
        senior["seniority_fit"] == "possible"
        or role["role_fit"] in {"other_stack", "unknown"}
        or hours["hours_likely_full_time"]
        or hours["hours_uncertain"]
        or location["location_fit"]
        in {"eu_remote_unverified", "remote_unverified", "authorization_unverified"}
        or (isinstance(score, (int, float)) and score < policy["weak_technical_threshold"])
        or row.get("recommendation") == "skip"
    ):
        priority = "LOW"
    if not hard and role["role_fit"] == "adjacent" and priority == "HIGH":
        priority = "MEDIUM"
    if not reasons:
        reason("compatible", "Hours, early-career role and location match stated preferences")
    eligibility = (
        "ineligible"
        if hard
        else "review_required"
        if any(item["code"].endswith(("review", "unknown", "uncertain")) for item in reasons)
        else "eligible"
    )
    return {
        **hours,
        **senior,
        **location,
        **role,
        "technical_match_score": score,
        "application_priority": priority,
        "application_priority_score": round(points, 1),
        "application_eligibility": eligibility,
        "eligibility_reasons": reasons,
        "eligibility_summary": "; ".join(item["detail"] for item in reasons)[:400],
        "exclusion_reason": "; ".join(item["detail"] for item in reasons if item["hard"]),
        "evidence_quality": evidence_quality,
        "eligibility_policy_version": policy["policy_id"],
    }


def application_sort_key(row):
    return (
        PRIORITY_ORDER.get(row.get("application_priority"), 2),
        -float(row.get("application_priority_score") or 0),
        -_stamp(row.get("published_at")),
        row.get("first_seen_at") or row.get("created_at", ""),
        row.get("dedupe_key", ""),
    )
