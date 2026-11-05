import fcntl
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from job_radar.ai.provider import AIProvider
from job_radar.candidate import candidate_profile_hash
from job_radar.enrichment import enrich_jobs
from job_radar.exporting import semantic_groups
from job_radar.http import HttpClient
from job_radar.matching import (
    SCORE_PROMPT_VERSION,
    application_queue,
    draft_jobs,
    score_jobs,
    scoring_candidates,
)
from job_radar.models import EmailMessage, Job
from job_radar.parsers import parse_email
from job_radar.storage import JobRepository
from job_radar.availability import group_expired
from job_radar.telegram import TelegramTransport, send_cards


class RunAlreadyActiveError(RuntimeError):
    pass


class RunLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.file: Any | None = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+")
        try:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.file.close()
            raise RunAlreadyActiveError("another job-radar run is active") from error
        return self

    def __exit__(self, *_: object) -> None:
        if self.file is not None:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()


@dataclass(frozen=True, slots=True)
class RunOptions:
    days: int = 2
    dry_run: bool = False
    no_telegram: bool = False


@dataclass(slots=True)
class RunDependencies:
    fetch_messages: Callable[[int], Iterable[EmailMessage]]
    http_client: HttpClient
    profile: dict[str, Any]
    ai_provider: AIProvider | None = None
    telegram_transport: TelegramTransport | None = None
    telegram_chat_id: str = ""
    input_cost_per_million: float = 0
    output_cost_per_million: float = 0


@dataclass(frozen=True, slots=True)
class RunReport:
    run_id: int
    status: str
    counts: dict[str, int]
    estimated_cost_usd: float
    errors: tuple[str, ...] = field(default_factory=tuple)


def _extract_safely(
    messages: Iterable[EmailMessage],
) -> tuple[list[Job], list[tuple[str, str]]]:
    unique: dict[str, Job] = {}
    errors: list[tuple[str, str]] = []
    for message in messages:
        try:
            for job in parse_email(message):
                unique.setdefault(job.dedupe_key, job)
        except Exception as error:
            errors.append((message.message_id, str(error)))
    return list(unique.values()), errors


def _active_semantic_keys(repository: JobRepository) -> set[str]:
    keys: set[str] = set()
    for group in semantic_groups(repository.all()):
        if group_expired(group):
            continue
        if any(
            row["screening_status"] in {"eligible", "review"} for row in group
        ):
            keys.add(str(group[0]["semantic_key"]))
    return keys


def run_pipeline(
    database_path: Path,
    options: RunOptions,
    dependencies: RunDependencies,
) -> RunReport:
    counts = {
        "emails": 0,
        "parsed": 0,
        "inserted": 0,
        "changed": 0,
        "unchanged": 0,
        "screened": 0,
        "rejected": 0,
        "semantic": 0,
        "enriched": 0,
        "scored": 0,
        "score_cache_hits": 0,
        "drafts": 0,
        "draft_cache_hits": 0,
        "telegram_sent": 0,
        "telegram_skipped_delivered": 0,
        "failed": 0,
    }
    errors: list[str] = []
    cost = 0.0
    lock_path = database_path.with_suffix(database_path.suffix + ".run.lock")
    with RunLock(lock_path), JobRepository(database_path) as repository:
        run_id = repository.start_run(
            options.days, options.dry_run, options.no_telegram
        )

        def stage_error(stage: str, error: Exception) -> None:
            errors.append(f"{stage}: {error}")
            counts["failed"] += 1
            repository.record_run_stage(
                run_id, stage, status="failed", error=str(error)
            )

        try:
            try:
                messages = list(dependencies.fetch_messages(options.days))
                counts["emails"] = len(messages)
                repository.record_run_stage(
                    run_id, "gmail", status="success", item_count=len(messages)
                )
            except Exception as error:
                messages = []
                stage_error("gmail", error)

            jobs, parse_errors = _extract_safely(messages)
            counts["parsed"] = len(jobs)
            for item_key, error in parse_errors:
                repository.record_run_item(
                    run_id, item_key, "parsing", "failed", error
                )
            counts["failed"] += len(parse_errors)
            repository.record_run_stage(
                run_id,
                "parsing",
                status="partial" if parse_errors else "success",
                item_count=len(jobs),
                error="; ".join(error for _, error in parse_errors),
            )

            changes = repository.classify(jobs)
            counts["inserted"] = changes.inserted
            counts["changed"] = changes.changed
            counts["unchanged"] = changes.unchanged
            changed_source_keys = set(changes.inserted_keys) | set(
                changes.changed_keys
            )

            if options.dry_run:
                repository.record_run_stage(
                    run_id,
                    "source_deduplication",
                    status="dry_run",
                    item_count=len(changed_source_keys),
                )
                for stage in (
                    "screening",
                    "semantic_grouping",
                    "enrichment",
                    "scoring",
                    "draft_generation",
                    "telegram",
                ):
                    repository.record_run_stage(run_id, stage, status="dry_run")
            else:
                repository.upsert_detailed(jobs)
                repository.record_run_stage(
                    run_id,
                    "source_deduplication",
                    status="success",
                    item_count=len(changed_source_keys),
                )

                screen_counts = {"eligible": 0, "review": 0, "rejected": 0}
                from job_radar.screening import load_rules
                version = load_rules()["version"]
                changed_source_keys.update(
                    row["dedupe_key"] for row in repository.all()
                    if row["screening_rules_version"] != version
                )
                for dedupe_key in sorted(changed_source_keys):
                    try:
                        result = repository.screen_keys([dedupe_key])
                        for status, value in result.items():
                            screen_counts[status] += value
                        repository.record_run_item(
                            run_id, dedupe_key, "screening", "success"
                        )
                    except Exception as error:
                        counts["failed"] += 1
                        repository.record_run_item(
                            run_id, dedupe_key, "screening", "failed", str(error)
                        )
                counts["screened"] = sum(screen_counts.values())
                counts["rejected"] = screen_counts["rejected"]
                repository.record_run_stage(
                    run_id,
                    "screening",
                    status=(
                        "partial"
                        if counts["screened"] < len(changed_source_keys)
                        else "success"
                    ),
                    item_count=counts["screened"],
                )

                active_keys = _active_semantic_keys(repository)
                counts["semantic"] = len(active_keys)
                repository.record_run_stage(
                    run_id,
                    "semantic_grouping",
                    status="success",
                    item_count=len(active_keys),
                )

                enrichment_failures = 0
                for semantic_key in sorted(active_keys):
                    try:
                        totals = enrich_jobs(
                            repository,
                            dependencies.http_client,
                            retry_failed=True,
                            semantic_keys={semantic_key},
                        )
                        complete = bool(
                            totals["success"] or totals["snippet_only"]
                        )
                        counts["enriched"] += int(complete)
                        enrichment_failures += int(not complete)
                        repository.record_run_item(
                            run_id,
                            semantic_key,
                            "enrichment",
                            "success" if complete else "failed",
                            "" if complete else "description unavailable",
                        )
                    except Exception as error:
                        enrichment_failures += 1
                        repository.record_run_item(
                            run_id,
                            semantic_key,
                            "enrichment",
                            "failed",
                            str(error),
                        )
                counts["failed"] += enrichment_failures
                repository.record_run_stage(
                    run_id,
                    "enrichment",
                    status="partial" if enrichment_failures else "success",
                    item_count=counts["enriched"],
                )

                profile_hash = candidate_profile_hash(dependencies.profile)
                scoring_failures = 0
                if dependencies.ai_provider is None and active_keys:
                    stage_error(
                        "scoring", RuntimeError("OPENAI_API_KEY is required")
                    )
                else:
                    provider = dependencies.ai_provider
                    for semantic_key in sorted(active_keys):
                        try:
                            candidates = scoring_candidates(
                                repository, None, {semantic_key}
                            )
                            if not candidates:
                                repository.record_run_item(
                                    run_id,
                                    semantic_key,
                                    "scoring",
                                    "skipped",
                                    "no scoreable description",
                                )
                                continue
                            vacancy = candidates[0]
                            failed_before = repository.failed_job_score(
                                semantic_key,
                                profile_hash,
                                str(vacancy["description_hash"]),
                                SCORE_PROMPT_VERSION,
                                provider.model,
                            )
                            summary = score_jobs(
                                repository,
                                provider,
                                dependencies.profile,
                                retry_failed=bool(failed_before),
                                input_cost_per_million=(
                                    dependencies.input_cost_per_million
                                ),
                                output_cost_per_million=(
                                    dependencies.output_cost_per_million
                                ),
                                semantic_keys={semantic_key},
                            )
                            counts["scored"] += summary.scored
                            counts["score_cache_hits"] += summary.cached
                            scoring_failures += summary.failed
                            cost += summary.estimated_cost_usd
                            repository.record_run_item(
                                run_id,
                                semantic_key,
                                "scoring",
                                "failed" if summary.failed else "success",
                                "; ".join(summary.errors),
                            )
                        except Exception as error:
                            scoring_failures += 1
                            repository.record_run_item(
                                run_id,
                                semantic_key,
                                "scoring",
                                "failed",
                                str(error),
                            )
                    counts["failed"] += scoring_failures
                    repository.record_run_stage(
                        run_id,
                        "scoring",
                        status="partial" if scoring_failures else "success",
                        item_count=(
                            counts["scored"] + counts["score_cache_hits"]
                        ),
                    )

                draft_failures = 0
                if dependencies.ai_provider is None and active_keys:
                    repository.record_run_stage(
                        run_id,
                        "draft_generation",
                        status="skipped",
                        error="scoring provider unavailable",
                    )
                else:
                    provider = dependencies.ai_provider
                    queue = {
                        row["semantic_key"]: row
                        for row in application_queue(
                            repository,
                            all_active=True,
                            profile_hash=profile_hash,
                            model=provider.model,
                        )
                    }
                    for semantic_key in sorted(active_keys):
                        item = queue.get(semantic_key)
                        if not item or item["score_status"] != "success":
                            continue
                        recommendation = item["recommendation"]
                        score = int(item["match_score"] or 0)
                        should_draft = recommendation in {
                            "strong_apply",
                            "apply",
                        } or (recommendation == "review" and score >= 50)
                        if not should_draft:
                            repository.record_run_item(
                                run_id,
                                semantic_key,
                                "draft_generation",
                                "skipped",
                            )
                            continue
                        try:
                            summary = draft_jobs(
                                repository,
                                provider,
                                dependencies.profile,
                                retry_failed=item["draft_status"] == "failed",
                                input_cost_per_million=(
                                    dependencies.input_cost_per_million
                                ),
                                output_cost_per_million=(
                                    dependencies.output_cost_per_million
                                ),
                                semantic_keys={semantic_key},
                                review_min_score=50,
                            )
                            counts["drafts"] += summary.ready
                            counts["draft_cache_hits"] += summary.cached
                            draft_failures += summary.failed
                            cost += summary.estimated_cost_usd
                            repository.record_run_item(
                                run_id,
                                semantic_key,
                                "draft_generation",
                                "failed" if summary.failed else "success",
                                "; ".join(summary.errors),
                            )
                        except Exception as error:
                            draft_failures += 1
                            repository.record_run_item(
                                run_id,
                                semantic_key,
                                "draft_generation",
                                "failed",
                                str(error),
                            )
                    counts["failed"] += draft_failures
                    repository.record_run_stage(
                        run_id,
                        "draft_generation",
                        status="partial" if draft_failures else "success",
                        item_count=(
                            counts["drafts"] + counts["draft_cache_hits"]
                        ),
                    )

                if options.no_telegram:
                    repository.record_run_stage(
                        run_id, "telegram", status="skipped"
                    )
                elif (
                    dependencies.telegram_transport is None
                    or not dependencies.telegram_chat_id
                ):
                    stage_error(
                        "telegram",
                        RuntimeError(
                            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required"
                        ),
                    )
                else:
                    provider_model = (
                        dependencies.ai_provider.model
                        if dependencies.ai_provider
                        else None
                    )
                    delivery = send_cards(
                        repository,
                        dependencies.telegram_transport,
                        dependencies.telegram_chat_id,
                        profile_hash=profile_hash,
                        model=provider_model,
                        semantic_keys=active_keys,
                    )
                    counts["telegram_sent"] = delivery.sent
                    counts["telegram_skipped_delivered"] = (
                        delivery.skipped_delivered
                    )
                    counts["failed"] += delivery.failed
                    repository.record_run_stage(
                        run_id,
                        "telegram",
                        status="partial" if delivery.failed else "success",
                        item_count=delivery.sent,
                    )
        except Exception as error:
            errors.append(str(error))
            counts["failed"] += 1

        status = "success" if counts["failed"] == 0 else "partial"
        repository.finish_run(
            run_id,
            status=status,
            counts=counts,
            estimated_cost_usd=cost,
            error="; ".join(errors),
        )
        return RunReport(run_id, status, counts, cost, tuple(errors))
