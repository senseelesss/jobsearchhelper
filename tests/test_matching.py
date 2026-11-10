from pathlib import Path
import json

from job_radar.ai.provider import (
    DraftProviderResult,
    ScoreProviderResult,
    TokenUsage,
)
from job_radar.application_models import ApplicationDraftContent, JobScore
from job_radar.candidate import candidate_profile_hash, load_candidate_profile
from job_radar.matching import (
    DRAFT_PROMPT_VERSION,
    SCORE_PROMPT_VERSION,
    application_queue,
    draft_cache_key,
    draft_jobs,
    funnel_counts,
    score_cache_key,
    score_jobs,
    scoring_candidates,
)
from job_radar.models import Job
from job_radar.storage import JobRepository


def words(count: int) -> str:
    return " ".join(f"word{index}" for index in range(count))


def score(confidence: str = "high", recommendation: str = "apply") -> JobScore:
    return JobScore(
        match_score=82,
        score_confidence=confidence,
        provisional=False,
        recommendation=recommendation,
        matching_strengths=["Python and API integration experience"],
        missing_requirements=["No explicit cloud platform experience"],
        risk_flags=[],
        role_summary="Backend and integration work for internal systems.",
    )


class FakeProvider:
    model = "test-model"

    def __init__(
        self,
        score_result: JobScore | None = None,
        first_draft_words: int = 200,
        rewrite_words: int = 200,
    ) -> None:
        self.score_result = score_result or score()
        self.first_draft_words = first_draft_words
        self.rewrite_words = rewrite_words
        self.score_calls = 0
        self.draft_calls = 0
        self.rewrite_calls = 0

    def score(
        self,
        candidate_profile: dict[str, object],
        vacancy: dict[str, object],
        provisional: bool,
    ) -> ScoreProviderResult:
        self.score_calls += 1
        return ScoreProviderResult(
            score=self.score_result,
            usage=TokenUsage(1000, 500, 1500),
            response_id="resp_score",
        )

    def draft(
        self,
        candidate_profile: dict[str, object],
        vacancy: dict[str, object],
        job_score: JobScore,
    ) -> DraftProviderResult:
        self.draft_calls += 1
        return DraftProviderResult(
            draft=ApplicationDraftContent(
                cover_letter_language="en",
                cover_letter=words(self.first_draft_words),
                recruiter_message="Factual recruiter message.",
            ),
            usage=TokenUsage(400, 300, 700),
            response_id="resp_draft",
        )

    def rewrite_draft(
        self,
        candidate_profile: dict[str, object],
        vacancy: dict[str, object],
        job_score: JobScore,
        draft: ApplicationDraftContent,
    ) -> DraftProviderResult:
        self.rewrite_calls += 1
        return DraftProviderResult(
            draft=ApplicationDraftContent(
                cover_letter_language="en",
                cover_letter=words(self.rewrite_words),
                recruiter_message=draft.recruiter_message,
            ),
            usage=TokenUsage(300, 250, 550),
            response_id="resp_rewrite",
        )


def insert_job(
    repository: JobRepository,
    source: str,
    source_job_id: str,
    *,
    screening_status: str,
    fetch_status: str = "",
    description: str = "",
    description_hash_value: str = "",
    received_at: str = "2026-06-23T08:00:00Z",
    fetch_error: str = "",
    company: str = "Example GmbH",
) -> Job:
    item = Job(
        source=source,
        source_job_id=source_job_id,
        title="Junior Backend Engineer",
        company=company,
        location="Vienna, Austria",
        snippet="Email snippet with Python and API work.",
        url=f"https://example.com/jobs/{source_job_id}",
        email_message_id=f"email-{source_job_id}",
        email_received_at=received_at,
        alert_subject="Jobs",
    )
    repository.upsert([item])
    repository.connection.execute(
        """
        UPDATE jobs
        SET screening_status = ?, fetch_status = ?, description = ?,
            description_hash = ?, description_language = 'en',
            description_confidence = ?, fetch_error = ?
        WHERE dedupe_key = ?
        """,
        (
            screening_status,
            fetch_status,
            description,
            description_hash_value,
            "low" if fetch_status != "success" else "high",
            fetch_error,
            item.dedupe_key,
        ),
    )
    repository.connection.commit()
    return item


def test_scoring_candidates_include_active_review_and_incomplete_but_not_expired(
    tmp_path,
) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    try:
        insert_job(
            repository,
            "linkedin",
            "eligible",
            screening_status="eligible",
            fetch_status="success",
            description="Full vacancy description",
            description_hash_value="full-hash",
        )
        insert_job(
            repository,
            "indeed",
            "review-incomplete",
            screening_status="review",
            company="Review GmbH",
        )
        insert_job(
            repository,
            "indeed",
            "expired",
            screening_status="review",
            fetch_status="snippet_only",
            fetch_error="expired listing",
            company="Expired GmbH",
        )

        candidates = scoring_candidates(repository, None)
    finally:
        repository.close()

    assert {row["source_job_id"] for row in candidates} == {
        "eligible",
        "review-incomplete",
    }
    incomplete = next(
        row for row in candidates if row["source_job_id"] == "review-incomplete"
    )
    assert incomplete["provisional"] is True
    assert incomplete["description_hash"]


def test_score_is_cached_and_snippet_score_is_forced_low_confidence(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    profile = load_candidate_profile(Path("candidate_profile.example.yaml"))
    provider = FakeProvider(score(confidence="high"))
    try:
        insert_job(
            repository,
            "indeed",
            "snippet",
            screening_status="eligible",
            fetch_status="snippet_only",
        )

        first = score_jobs(
            repository,
            provider,
            profile,
            only="eligible",
            input_cost_per_million=1,
            output_cost_per_million=2,
        )
        second = score_jobs(repository, provider, profile, only="eligible")
        rows = repository.latest_job_scores()
    finally:
        repository.close()

    assert first.scored == 1
    assert first.estimated_cost_usd == 0.002
    assert second.cached == 1
    assert provider.score_calls == 1
    stored = JobScore.model_validate_json(rows[0]["result_json"])
    assert stored.score_confidence == "low"
    assert stored.provisional is True


def test_draft_rewrites_once_and_keeps_stored_score(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    profile = load_candidate_profile(Path("candidate_profile.example.yaml"))
    provider = FakeProvider(first_draft_words=120, rewrite_words=180)
    try:
        insert_job(
            repository,
            "linkedin",
            "rewrite",
            screening_status="eligible",
            fetch_status="success",
            description="Full vacancy description",
            description_hash_value="full-hash",
        )
        score_jobs(repository, provider, profile, only="eligible")
        summary = draft_jobs(repository, provider, profile)
        score_rows = repository.latest_job_scores()
        draft = repository.latest_application_draft(
            score_rows[0]["score_cache_key"]
        )
    finally:
        repository.close()

    assert summary.ready == 1
    assert provider.score_calls == 1
    assert provider.draft_calls == 1
    assert provider.rewrite_calls == 1
    assert score_rows[0]["score_status"] == "success"
    assert draft["draft_status"] == "ready"
    assert draft["rewrite_attempted"] == 1


def test_failed_rewrite_does_not_discard_or_repeat_score(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    profile = load_candidate_profile(Path("candidate_profile.example.yaml"))
    provider = FakeProvider(first_draft_words=120, rewrite_words=120)
    try:
        insert_job(
            repository,
            "linkedin",
            "draft-failure",
            screening_status="eligible",
            fetch_status="success",
            description="Full vacancy description",
            description_hash_value="full-hash",
        )
        score_jobs(repository, provider, profile, only="eligible")
        first = draft_jobs(repository, provider, profile)
        second_score = score_jobs(repository, provider, profile, only="eligible")
        score_row = repository.latest_job_scores()[0]
        draft = repository.latest_application_draft(
            score_row["score_cache_key"]
        )
    finally:
        repository.close()

    assert first.failed == 1
    assert second_score.cached == 1
    assert provider.score_calls == 1
    assert score_row["score_status"] == "success"
    assert draft["draft_status"] == "failed"
    assert "160-290" in draft["draft_error"]


def test_application_queue_and_funnel_include_unscored_active_jobs(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    try:
        insert_job(
            repository,
            "linkedin",
            "unscored",
            screening_status="review",
        )
        queue = application_queue(repository, all_active=True)
        funnel = funnel_counts(repository)
    finally:
        repository.close()

    assert len(queue) == 1
    assert queue[0]["score_status"] == "not_started"
    assert queue[0]["draft_status"] == "not_started"
    assert queue[0]["vacancy_state"] == "active"
    assert funnel["source_jobs"] == 1
    assert funnel["review"] == 1


def test_required_cache_keys_are_stable() -> None:
    profile = load_candidate_profile(Path("candidate_profile.example.yaml"))
    profile_hash = candidate_profile_hash(profile)
    score_key = score_cache_key(
        "semantic-example",
        profile_hash,
        "description-hash",
        SCORE_PROMPT_VERSION,
        "test-model",
    )

    assert len(score_key) == 64
    assert score_key == score_cache_key(
        "semantic-example",
        profile_hash,
        "description-hash",
        SCORE_PROMPT_VERSION,
        "test-model",
    )
    assert draft_cache_key(
        score_key, DRAFT_PROMPT_VERSION, "test-model"
    ) == draft_cache_key(score_key, DRAFT_PROMPT_VERSION, "test-model")


def test_score_cache_key_includes_semantic_key() -> None:
    common = (
        "profile-hash",
        "description-hash",
        SCORE_PROMPT_VERSION,
        "test-model",
    )

    assert score_cache_key("semantic-one", *common) != score_cache_key(
        "semantic-two", *common
    )


def test_application_queue_rejects_stale_score_after_description_change(
    tmp_path,
) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    profile = load_candidate_profile(Path("candidate_profile.example.yaml"))
    provider = FakeProvider()
    try:
        item = insert_job(
            repository,
            "linkedin",
            "stale-description",
            screening_status="eligible",
            fetch_status="success",
            description="Original description",
            description_hash_value="ignored",
        )
        score_jobs(repository, provider, profile)
        assert application_queue(
            repository,
            profile_hash=candidate_profile_hash(profile),
            model=provider.model,
        )
        assert application_queue(
            repository,
            profile_hash="changed-profile",
            model=provider.model,
        ) == []
        assert application_queue(
            repository,
            profile_hash=candidate_profile_hash(profile),
            model="changed-model",
        ) == []

        repository.connection.execute(
            "UPDATE job_scores SET prompt_version = 'legacy-prompt'"
        )
        repository.connection.commit()
        assert application_queue(
            repository,
            profile_hash=candidate_profile_hash(profile),
            model=provider.model,
        ) == []
        repository.connection.execute(
            "UPDATE job_scores SET prompt_version = ?",
            (SCORE_PROMPT_VERSION,),
        )
        repository.connection.commit()

        repository.connection.execute(
            """
            UPDATE jobs
            SET description = 'Changed description',
                description_hash = 'changed'
            WHERE dedupe_key = ?
            """,
            (item.dedupe_key,),
        )
        repository.connection.commit()

        assert application_queue(
            repository,
            profile_hash=candidate_profile_hash(profile),
            model=provider.model,
        ) == []
    finally:
        repository.close()


def test_application_queue_rejects_stale_draft_prompt_or_model(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    profile = load_candidate_profile(Path("candidate_profile.example.yaml"))
    provider = FakeProvider()
    try:
        insert_job(
            repository,
            "linkedin",
            "stale-draft",
            screening_status="eligible",
            fetch_status="success",
            description="Current description",
            description_hash_value="ignored",
        )
        score_jobs(repository, provider, profile)
        draft_jobs(repository, provider, profile)
        current = application_queue(
            repository,
            profile_hash=candidate_profile_hash(profile),
            model=provider.model,
        )[0]
        assert current["draft_status"] == "ready"

        repository.connection.execute(
            "UPDATE application_drafts SET prompt_version = 'legacy-draft'"
        )
        repository.connection.commit()
        stale_prompt = application_queue(
            repository,
            profile_hash=candidate_profile_hash(profile),
            model=provider.model,
        )[0]
        assert stale_prompt["draft_status"] == "not_started"
        assert stale_prompt["cover_letter"] == ""

        repository.connection.execute(
            "UPDATE application_drafts SET prompt_version = ?, model = 'old-model'",
            (DRAFT_PROMPT_VERSION,),
        )
        repository.connection.commit()
        stale_model = application_queue(
            repository,
            profile_hash=candidate_profile_hash(profile),
            model=provider.model,
        )[0]
        assert stale_model["draft_status"] == "not_started"
    finally:
        repository.close()


def test_retry_failed_scores_only_previous_failures(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    profile = load_candidate_profile(Path("candidate_profile.example.yaml"))
    provider = FakeProvider()
    try:
        insert_job(
            repository,
            "linkedin",
            "failed-before",
            screening_status="eligible",
            fetch_status="success",
            description="Failed vacancy description",
            description_hash_value="failed-hash",
            company="Failed GmbH",
        )
        insert_job(
            repository,
            "linkedin",
            "never-attempted",
            screening_status="eligible",
            fetch_status="success",
            description="New vacancy description",
            description_hash_value="new-hash",
            company="New GmbH",
        )
        failed_vacancy = next(
            row
            for row in scoring_candidates(repository, "eligible")
            if row["source_job_id"] == "failed-before"
        )
        repository.save_job_score(
            {
                "score_cache_key": "legacy-failure",
                "semantic_key": failed_vacancy["semantic_key"],
                "canonical_dedupe_key": failed_vacancy["dedupe_key"],
                "candidate_profile_hash": candidate_profile_hash(profile),
                "description_hash": failed_vacancy["description_hash"],
                "prompt_version": SCORE_PROMPT_VERSION,
                "model": provider.model,
                "provisional": 0,
                "result_json": "{}",
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0,
                "response_id": "",
                "score_status": "failed",
                "score_error": "legacy validation failure",
                "created_at": "2026-06-23T08:00:00+00:00",
            }
        )

        summary = score_jobs(
            repository,
            provider,
            profile,
            only="eligible",
            retry_failed=True,
        )
    finally:
        repository.close()

    assert summary.scored == 1
    assert provider.score_calls == 1


def test_legacy_packages_migrate_to_separate_scores_and_drafts(tmp_path) -> None:
    database_path = tmp_path / "jobs.db"
    repository = JobRepository(database_path)
    item = insert_job(
        repository,
        "linkedin",
        "legacy-package",
        screening_status="eligible",
        fetch_status="success",
        description="Full description",
        description_hash_value="description-hash",
    )
    legacy_result = {
        **score().model_dump(exclude={"provisional"}),
        "cover_letter_language": "en",
        "cover_letter": words(200),
        "recruiter_message": "Factual recruiter message.",
    }
    repository.save_application_package(
        {
            "cache_key": "legacy-success",
            "semantic_key": item.semantic_key,
            "canonical_dedupe_key": item.dedupe_key,
            "candidate_profile_hash": "profile-hash",
            "description_hash": "description-hash",
            "prompt_version": "candidate-match-v1",
            "model": "test-model",
            "provisional": 0,
            "result_json": json.dumps(legacy_result),
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "estimated_cost_usd": 0.001,
            "response_id": "resp_legacy",
            "status": "success",
            "error": "",
            "created_at": "2026-06-23T08:00:00+00:00",
        }
    )
    repository.close()

    migrated = JobRepository(database_path)
    try:
        score_row = migrated.cached_job_score("legacy-success")
        draft = migrated.latest_application_draft("legacy-success")
    finally:
        migrated.close()

    assert score_row["score_status"] == "success"
    assert "cover_letter" not in json.loads(score_row["result_json"])
    assert draft["draft_status"] == "ready"
    assert json.loads(draft["result_json"])["cover_letter"] == words(200)
