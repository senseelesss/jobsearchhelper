from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from job_radar.application_models import ApplicationDraftContent

from job_radar.ai.provider import AIProvider, TokenUsage
from job_radar.candidate import candidate_profile_hash
from job_radar.enrichment_adapters.common import description_hash
from job_radar.exporting import semantic_groups
from job_radar.storage import JobRepository
from job_radar.availability import group_expired, is_expired
from job_radar.errors import error_label
from job_radar.eligibility import assess_application

SCORE_PROMPT_VERSION = "candidate-score-v3-hours-independent"
DRAFT_PROMPT_VERSION = "application-draft-v1"
APPLICATION_RECOMMENDATIONS = {"strong_apply", "apply", "review"}


@dataclass(frozen=True, slots=True)
class ScoreSummary:
    scored: int
    cached: int
    failed: int
    skipped: int
    estimated_cost_usd: float
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DraftSummary:
    ready: int
    cached: int
    failed: int
    not_applicable: int
    estimated_cost_usd: float
    errors: tuple[str, ...] = ()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_expired(row: dict[str, Any]) -> bool:
    return is_expired(row)


def _effective_vacancy(row: dict[str, Any]) -> dict[str, Any]:
    value = dict(row)
    if row["description"]:
        effective_description = str(row["description"])
    elif row["snippet"]:
        effective_description = str(row["snippet"])
    else:
        effective_description = (
            f"Role: {row['title']}\nCompany: {row['company']}\n"
            f"Location: {row['location']}\nFull requirements unavailable."
        )
    value["description"] = effective_description
    value["description_hash"] = description_hash(effective_description)
    value["provisional"] = (
        row["fetch_status"] != "success"
        or row["description_confidence"] not in {"high", "medium"}
        or not row["description"]
        or str(row.get("fetch_error", "")).startswith("Refresh failed")
    )
    return value


def scoring_candidates(
    repository: JobRepository,
    only: str | None,
    semantic_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    statuses = ("eligible", "review") if only is None else (only,)
    status_set = set(statuses)
    candidates: list[dict[str, Any]] = []
    for group in semantic_groups(repository.all()):
        if semantic_keys is not None and str(group[0]["semantic_key"]) not in semantic_keys:
            continue
        in_scope = [row for row in group if row["screening_status"] in status_set]
        if not in_scope:
            continue
        if group_expired(group):
            continue
        candidate = _effective_vacancy(in_scope[0])
        candidate["created_at"] = min(group, key=_first_seen)["created_at"]
        candidates.append(candidate)
    return candidates


def score_cache_key(
    semantic_key: str,
    profile_hash: str,
    vacancy_description_hash: str,
    prompt_version: str,
    model: str,
) -> str:
    value = "\n".join(
        (
            semantic_key,
            profile_hash,
            vacancy_description_hash,
            prompt_version,
            model,
        )
    )
    return hashlib.sha256(value.encode()).hexdigest()


def draft_cache_key(
    score_key: str,
    prompt_version: str,
    model: str,
) -> str:
    return hashlib.sha256("\n".join((score_key, prompt_version, model)).encode()).hexdigest()


def _estimated_cost(
    usage: TokenUsage,
    input_cost_per_million: float,
    output_cost_per_million: float,
) -> float:
    return round(
        usage.input_tokens * input_cost_per_million / 1_000_000
        + usage.output_tokens * output_cost_per_million / 1_000_000,
        8,
    )


def has_full_description(row):
    return not _effective_vacancy(row)["provisional"]


def _first_seen(row):
    try:
        value = datetime.fromisoformat(str(row.get("created_at", "")).replace("Z", "+00:00"))
        return value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp()
    except ValueError:
        return 0.0


def scoring_plan(
    repository, profile, model, *, only="eligible", limit=None, semantic_keys=None,
    retry_failed=False, retry_after_seconds=None, new_semantic_keys=None, fairness_turn=0,
):
    """Plan actual calls; cache hits and cooling failures never consume call slots."""
    profile_hash = candidate_profile_hash(profile)
    pending, cached, deferred = [], 0, 0
    now = datetime.now(timezone.utc)
    for vacancy in scoring_candidates(repository, only, semantic_keys):
        vacancy['application_state'] = repository.application_for(vacancy['dedupe_key'])['state']
        vacancy.update(assess_application(vacancy, profile))
        if vacancy['application_eligibility']=='ineligible':
            continue
        identity = (vacancy["semantic_key"], profile_hash, vacancy["description_hash"],
                    SCORE_PROMPT_VERSION, model)
        if repository.reusable_job_score(*identity):
            cached += 1
            continue
        failure = repository.failed_job_score(*identity)
        if retry_failed and failure is None:
            continue
        if failure and not retry_failed:
            stamp = datetime.fromisoformat(failure["created_at"].replace("Z", "+00:00"))
            stamp = stamp.replace(tzinfo=stamp.tzinfo or timezone.utc)
            if retry_after_seconds is None or (now-stamp).total_seconds() < retry_after_seconds:
                deferred += 1
                continue
        pending.append(vacancy)
    new_keys = new_semantic_keys or set()

    def priority(row):
        return (
            {'confirmed_target':0,'acceptable':1,'flexible_part_time':2,'unknown':3}.get(row['hours_fit'],4),
            {'student':0,'junior':1,'internship':2,'compatible':3,'unspecified':4,'possible':5}.get(row['seniority_fit'],6),
            {'strong':0,'relevant':1,'secondary':2,'adjacent':3,'unknown':4,'other_stack':5}.get(row['role_fit'],6),
            row["provisional"],
            row["semantic_key"] not in new_keys,
            -_first_seen(row), row["dedupe_key"],
        )

    pending.sort(key=priority)
    # Reserve one FIFO backlog slot, rather than letting aged generalists consume
    # the whole budget ahead of newly discovered target-hours student jobs.
    oldest = sorted(pending, key=lambda row: (_first_seen(row), row["dedupe_key"]))
    selected = pending[:limit] if limit is not None else list(pending)
    backlog = [row for row in oldest if row["semantic_key"] not in new_keys
               and _first_seen(row) <= now.timestamp()-86400]
    if selected and backlog and (len(selected) >= 2 or fairness_turn % 3 == 0):
        if backlog[0] not in selected:
            selected[-1] = backlog[0]
    return selected, {"cached": cached, "deferred": deferred + len(pending)-len(selected)}


def score_jobs(
    repository: JobRepository,
    provider: AIProvider,
    profile: dict[str, Any],
    only: str | None = None,
    limit: int | None = None,
    retry_failed: bool = False,
    input_cost_per_million: float = 0,
    output_cost_per_million: float = 0,
    semantic_keys: set[str] | None = None,
    retry_after_seconds: float | None = None,
    new_semantic_keys: set[str] | None = None,
    fairness_turn: int = 0,
) -> ScoreSummary:
    profile_hash = candidate_profile_hash(profile)
    candidates, planned = scoring_plan(
        repository, profile, provider.model, only=only, limit=limit,
        semantic_keys=semantic_keys, retry_failed=retry_failed,
        retry_after_seconds=retry_after_seconds, new_semantic_keys=new_semantic_keys,
        fairness_turn=fairness_turn,
    )
    scored = failed = 0
    cached, skipped = planned["cached"], planned["deferred"]
    estimated_cost_usd = 0.0
    errors: list[str] = []
    for vacancy in candidates:
        provisional = bool(vacancy["provisional"])
        cache_key = score_cache_key(
            str(vacancy["semantic_key"]),
            profile_hash,
            str(vacancy["description_hash"]),
            SCORE_PROMPT_VERSION,
            provider.model,
        )
        reusable = repository.reusable_job_score(
            str(vacancy["semantic_key"]),
            profile_hash,
            str(vacancy["description_hash"]),
            SCORE_PROMPT_VERSION,
            provider.model,
        )
        if reusable:
            cached += 1
            continue
        failed_attempt = repository.failed_job_score(
            str(vacancy["semantic_key"]),
            profile_hash,
            str(vacancy["description_hash"]),
            SCORE_PROMPT_VERSION,
            provider.model,
        )
        if failed_attempt and not retry_failed:
            failed_at = datetime.fromisoformat(failed_attempt["created_at"])
            if failed_at.tzinfo is None:
                failed_at = failed_at.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - failed_at).total_seconds()
            if retry_after_seconds is None or age < retry_after_seconds:
                skipped += 1
                continue

        created_at = _now()
        try:
            result = provider.score(profile, vacancy, provisional)
            score = result.score.model_copy(
                update={
                    "provisional": provisional,
                    "score_confidence": ("low" if provisional else result.score.score_confidence),
                }
            )
            cost = _estimated_cost(
                result.usage,
                input_cost_per_million,
                output_cost_per_million,
            )
            repository.save_job_score(
                {
                    "score_cache_key": cache_key,
                    "semantic_key": vacancy["semantic_key"],
                    "canonical_dedupe_key": vacancy["dedupe_key"],
                    "candidate_profile_hash": profile_hash,
                    "description_hash": vacancy["description_hash"],
                    "prompt_version": SCORE_PROMPT_VERSION,
                    "model": provider.model,
                    "provisional": int(provisional),
                    "result_json": score.model_dump_json(),
                    "input_tokens": result.usage.input_tokens,
                    "output_tokens": result.usage.output_tokens,
                    "total_tokens": result.usage.total_tokens,
                    "estimated_cost_usd": cost,
                    "response_id": result.response_id,
                    "score_status": "success",
                    "score_error": "",
                    "created_at": created_at,
                }
            )
            scored += 1
            estimated_cost_usd += cost
        except Exception as error:
            errors.append(error_label(error))
            repository.save_job_score(
                {
                    "score_cache_key": cache_key,
                    "semantic_key": vacancy["semantic_key"],
                    "canonical_dedupe_key": vacancy["dedupe_key"],
                    "candidate_profile_hash": profile_hash,
                    "description_hash": vacancy["description_hash"],
                    "prompt_version": SCORE_PROMPT_VERSION,
                    "model": provider.model,
                    "provisional": int(provisional),
                    "result_json": "{}",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "estimated_cost_usd": 0,
                    "response_id": "",
                    "score_status": "failed",
                    "score_error": error_label(error),
                    "created_at": created_at,
                }
            )
            failed += 1

    return ScoreSummary(
        scored,
        cached,
        failed,
        skipped,
        estimated_cost_usd,
        tuple(errors),
    )


def _word_count(value: str) -> int:
    return len(value.split())


def _valid_draft_length(draft: ApplicationDraftContent) -> bool:
    return 160 <= _word_count(draft.cover_letter) <= 290


def draft_jobs(
    repository: JobRepository,
    provider: AIProvider,
    profile: dict[str, Any],
    recommendations: set[str] | None = None,
    retry_failed: bool = False,
    limit: int | None = None,
    input_cost_per_million: float = 0,
    output_cost_per_million: float = 0,
    semantic_keys: set[str] | None = None,
    review_min_score: int = 0,
) -> DraftSummary:
    from job_radar.application_models import JobScore
    accepted = recommendations or APPLICATION_RECOMMENDATIONS
    profile_hash = candidate_profile_hash(profile)
    all_score_rows = [
        row
        for row in repository.latest_job_scores()
        if row["score_status"] == "success"
        and (semantic_keys is None or row["semantic_key"] in semantic_keys)
        and _score_row_is_current(row, profile_hash, provider.model)
    ]
    score_rows: list[dict[str, Any]] = []
    not_applicable = 0
    cached = 0
    failed = 0
    for row in all_score_rows:
        score = JobScore.model_validate_json(row["result_json"])
        existing = repository.latest_application_draft(row["score_cache_key"])
        if existing and (
            existing["prompt_version"] != DRAFT_PROMPT_VERSION
            or existing["model"] != provider.model
        ):
            existing = None
        applies = score.recommendation in accepted and not (
            score.recommendation == "review" and score.match_score < review_min_score
        )
        if not applies:
            if not existing:
                repository.save_application_draft(
                    _draft_values(
                        row,
                        draft_cache_key(
                            row["score_cache_key"],
                            DRAFT_PROMPT_VERSION,
                            provider.model,
                        ),
                        provider.model,
                        {},
                        "not_applicable",
                        "",
                        TokenUsage(),
                        0,
                        "",
                        False,
                    )
                )
            not_applicable += 1
            continue
        if existing and existing["draft_status"] == "ready":
            cached += 1
            continue
        if retry_failed:
            if existing and existing["draft_status"] == "failed":
                score_rows.append(row)
            continue
        if existing and existing["draft_status"] == "failed":
            failed += 1
            continue
        score_rows.append(row)

    if limit is not None:
        score_rows = score_rows[:limit]

    ready = 0
    estimated_cost_usd = 0.0
    errors: list[str] = []
    for row in score_rows:
        score = JobScore.model_validate_json(row["result_json"])
        key = draft_cache_key(
            row["score_cache_key"],
            DRAFT_PROMPT_VERSION,
            provider.model,
        )

        vacancy = _effective_vacancy(row)
        usage = TokenUsage()
        response_ids: list[str] = []
        rewrite_attempted = False
        try:
            first = provider.draft(profile, vacancy, score)
            draft = first.draft
            usage = _add_usage(usage, first.usage)
            response_ids.append(first.response_id)
            if not _valid_draft_length(draft):
                rewrite_attempted = True
                rewritten = provider.rewrite_draft(profile, vacancy, score, draft)
                draft = rewritten.draft
                usage = _add_usage(usage, rewritten.usage)
                response_ids.append(rewritten.response_id)

            cost = _estimated_cost(
                usage,
                input_cost_per_million,
                output_cost_per_million,
            )
            if not _valid_draft_length(draft):
                error = (
                    "Cover letter remained outside accepted 160-290 word range "
                    f"after rewrite: {_word_count(draft.cover_letter)} words"
                )
                status = "failed"
                result_json = draft.model_dump()
                failed += 1
            else:
                error = ""
                status = "ready"
                result_json = draft.model_dump()
                ready += 1
            repository.save_application_draft(
                _draft_values(
                    row,
                    key,
                    provider.model,
                    result_json,
                    status,
                    error,
                    usage,
                    cost,
                    ",".join(value for value in response_ids if value),
                    rewrite_attempted,
                )
            )
            estimated_cost_usd += cost
        except Exception as error:
            errors.append(error_label(error))
            repository.save_application_draft(
                _draft_values(
                    row,
                    key,
                    provider.model,
                    {},
                    "failed",
                    error_label(error),
                    usage,
                    0,
                    ",".join(value for value in response_ids if value),
                    rewrite_attempted,
                )
            )
            failed += 1

    return DraftSummary(
        ready,
        cached,
        failed,
        not_applicable,
        estimated_cost_usd,
        tuple(errors),
    )


def _add_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    return TokenUsage(
        left.input_tokens + right.input_tokens,
        left.output_tokens + right.output_tokens,
        left.total_tokens + right.total_tokens,
    )


def _draft_values(
    score_row: dict[str, Any],
    key: str,
    model: str,
    result: dict[str, Any],
    status: str,
    error: str,
    usage: TokenUsage,
    cost: float,
    response_id: str,
    rewrite_attempted: bool,
) -> dict[str, Any]:
    return {
        "draft_cache_key": key,
        "score_cache_key": score_row["score_cache_key"],
        "semantic_key": score_row["semantic_key"],
        "canonical_dedupe_key": score_row["canonical_dedupe_key"],
        "prompt_version": DRAFT_PROMPT_VERSION,
        "model": model,
        "result_json": json.dumps(result, ensure_ascii=False),
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "estimated_cost_usd": cost,
        "response_id": response_id,
        "draft_status": status,
        "draft_error": error,
        "rewrite_attempted": int(rewrite_attempted),
        "created_at": _now(),
    }


def application_queue(
    repository: JobRepository,
    all_active: bool = False,
    profile_hash: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    statuses = ("eligible", "review")
    groups = semantic_groups(repository.all())
    scores = {
        row["semantic_key"]: row
        for row in repository.latest_job_scores()
        if _score_row_is_current(row, profile_hash, model)
    }
    queue: list[dict[str, Any]] = []
    for group in groups:
        in_scope = [row for row in group if row["screening_status"] in statuses]
        if not in_scope:
            continue
        vacancy = in_scope[0]
        expired = group_expired(group)
        score_row = scores.get(vacancy["semantic_key"])
        if not all_active and not score_row:
            continue

        score_data: dict[str, Any] = {}
        draft_data: dict[str, Any] = {}
        draft_status = "not_started"
        draft_error = ""
        score_status = "not_started"
        score_error = ""
        model = ""
        scored_at = ""
        draft_at = ""
        input_tokens = output_tokens = total_tokens = 0
        estimated_cost = 0.0
        provisional = True
        if score_row:
            score_status = score_row["score_status"]
            score_error = score_row["score_error"]
            model = score_row["model"]
            scored_at = score_row["created_at"]
            provisional = bool(score_row["provisional"])
            input_tokens += score_row["input_tokens"]
            output_tokens += score_row["output_tokens"]
            total_tokens += score_row["total_tokens"]
            estimated_cost += score_row["estimated_cost_usd"]
            if score_status == "success":
                score_data = json.loads(score_row["result_json"])
                draft = repository.latest_application_draft(score_row["score_cache_key"])
                if draft and (
                    draft["prompt_version"] != DRAFT_PROMPT_VERSION
                    or draft["model"] != score_row["model"]
                ):
                    draft = None
                if draft:
                    draft_status = draft["draft_status"]
                    draft_error = draft["draft_error"]
                    draft_at = draft["created_at"]
                    input_tokens += draft["input_tokens"]
                    output_tokens += draft["output_tokens"]
                    total_tokens += draft["total_tokens"]
                    estimated_cost += draft["estimated_cost_usd"]
                    if draft["result_json"] != "{}":
                        draft_data = json.loads(draft["result_json"])

        queue.append(
            {
                "semantic_key": vacancy["semantic_key"],
                "title": vacancy["title"],
                "company": vacancy["company"],
                "location": vacancy["location"],
                "url": vacancy["final_url"] or vacancy["url"],
                "screening_status": vacancy["screening_status"],
                "vacancy_state": "expired" if expired else "active",
                "description_confidence": vacancy["description_confidence"],
                "description_language": vacancy["description_language"],
                "score_status": score_status,
                "score_error": score_error,
                "draft_status": draft_status,
                "draft_error": draft_error,
                "score_cache_key": (score_row["score_cache_key"] if score_row else ""),
                "provisional": provisional,
                "match_score": score_data.get("match_score"),
                "score_confidence": score_data.get("score_confidence", ""),
                "recommendation": score_data.get("recommendation", ""),
                "matching_strengths": score_data.get("matching_strengths", []),
                "missing_requirements": score_data.get("missing_requirements", []),
                "risk_flags": score_data.get("risk_flags", []),
                "role_summary": score_data.get("role_summary", ""),
                "cover_letter_language": draft_data.get("cover_letter_language", ""),
                "cover_letter": draft_data.get("cover_letter", ""),
                "recruiter_message": draft_data.get("recruiter_message", ""),
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "estimated_cost_usd": round(estimated_cost, 8),
                "scored_at": scored_at,
                "drafted_at": draft_at,
                "email_received_at": vacancy["email_received_at"],
            }
        )

    recommendation_order = {
        "strong_apply": 0,
        "apply": 1,
        "review": 2,
        "skip": 3,
        "": 4,
    }
    return sorted(
        queue,
        key=lambda row: (
            row["vacancy_state"] != "active",
            recommendation_order.get(row.get("recommendation", ""), 4),
            -int(row.get("match_score") or -1),
            -_timestamp(str(row["email_received_at"])),
        ),
    )


def funnel_counts(
    repository: JobRepository,
    profile_hash: str | None = None,
    model: str | None = None,
) -> dict[str, int | float]:
    jobs = repository.all()
    groups = semantic_groups(jobs)
    canonical = [group[0] for group in groups]
    eligible_keys = {row["semantic_key"] for row in jobs if row["screening_status"] == "eligible"}
    review_keys = {
        row["semantic_key"] for row in jobs if row["screening_status"] == "review"
    } - eligible_keys
    rejected_keys = {row["semantic_key"] for row in jobs} - eligible_keys - review_keys
    scores = [
        row
        for row in repository.latest_job_scores()
        if _score_row_is_current(row, profile_hash, model)
    ]
    drafts = repository.all_application_drafts()
    current_score_keys = {row["score_cache_key"] for row in scores}

    latest_drafts: dict[str, dict[str, Any]] = {}
    for draft in drafts:
        if draft["score_cache_key"] not in current_score_keys:
            continue
        latest_drafts.setdefault(draft["score_cache_key"], draft)
    successful_scores = [row for row in scores if row["score_status"] == "success"]
    failed_scores = [row for row in scores if row["score_status"] == "failed"]
    return {
        "source_jobs": len(jobs),
        "semantic_vacancies": len(groups),
        "eligible": len(eligible_keys),
        "review": len(review_keys),
        "rejected": len(rejected_keys),
        "expired": sum(group_expired(group) for group in groups),
        "enriched_full": sum(row["fetch_status"] == "success" for row in canonical),
        "snippet_only": sum(row["fetch_status"] == "snippet_only" for row in canonical),
        "scored": len(successful_scores),
        "score_failed": len(failed_scores),
        "draft_ready": sum(draft["draft_status"] == "ready" for draft in latest_drafts.values()),
        "draft_failed": sum(draft["draft_status"] == "failed" for draft in latest_drafts.values()),
        "estimated_api_cost": round(
            sum(row["estimated_cost_usd"] for row in scores)
            + sum(draft["estimated_cost_usd"] for draft in drafts),
            8,
        ),
    }


def _score_row_is_current(
    row: dict[str, Any],
    profile_hash: str | None,
    model: str | None,
) -> bool:
    if row["prompt_version"] != SCORE_PROMPT_VERSION:
        return False
    if profile_hash is not None and row["candidate_profile_hash"] != profile_hash:
        return False
    if model is not None and row["model"] != model:
        return False
    return row["description_hash"] == _effective_vacancy(row)["description_hash"]


def _timestamp(value: str) -> float:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()
