import json

from job_radar.enrichment_adapters.common import description_hash
from job_radar.matching import DRAFT_PROMPT_VERSION, SCORE_PROMPT_VERSION
from job_radar.models import Job
from job_radar.storage import JobRepository
from job_radar.telegram import (
    card_keyboard,
    preview_cards,
    process_callback,
    process_updates,
    render_card,
    send_cards,
    telegram_candidates,
)


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, object] | None]] = []
        self.edited: list[tuple[str, int, str, dict[str, object] | None]] = []
        self.answered: list[tuple[str, str]] = []

    def send_message(
        self,
        chat_id: str,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> int:
        self.sent.append((chat_id, text, reply_markup))
        return 100 + len(self.sent)

    def edit_message(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> None:
        self.edited.append((chat_id, message_id, text, reply_markup))

    def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        self.answered.append((callback_query_id, text))

    def get_updates(
        self, offset: int | None = None, timeout: int = 25
    ) -> list[dict[str, object]]:
        return []


def seed_queue_item(
    repository: JobRepository,
    source_job_id: str,
    recommendation: str,
    score: int,
    *,
    score_status: str = "success",
    expired: bool = False,
    draft_ready: bool = True,
) -> str:
    item = Job(
        source="linkedin",
        source_job_id=source_job_id,
        title=f"Role {source_job_id}",
        company=f"Company {source_job_id}",
        location="Vienna, Austria (Hybrid)",
        snippet="",
        url=f"https://www.linkedin.com/jobs/view/{source_job_id}/",
        email_message_id=f"email-{source_job_id}",
        email_received_at="2026-06-23T08:00:00Z",
        alert_subject="Jobs",
    )
    repository.upsert([item])
    repository.connection.execute(
        """
        UPDATE jobs
        SET screening_status = 'eligible',
            fetch_status = ?,
            fetch_error = ?,
            description = 'Full description',
            description_hash = ?,
            description_language = 'de',
            description_confidence = 'medium',
            final_url = url
        WHERE dedupe_key = ?
        """,
        (
            "expired" if expired else "success",
            "expired listing" if expired else "",
            description_hash("Full description"),
            item.dedupe_key,
        ),
    )
    repository.connection.commit()
    score_key = f"score-{source_job_id}"
    repository.save_job_score(
        {
            "score_cache_key": score_key,
            "semantic_key": item.semantic_key,
            "canonical_dedupe_key": item.dedupe_key,
            "candidate_profile_hash": "profile",
            "description_hash": description_hash("Full description"),
            "prompt_version": SCORE_PROMPT_VERSION,
            "model": "test-model",
            "provisional": 0,
            "result_json": json.dumps(
                {
                    "match_score": score,
                    "score_confidence": "high",
                    "provisional": False,
                    "recommendation": recommendation,
                    "matching_strengths": [
                        "Python integrations",
                        "Backend automation",
                        "Vienna location",
                        "German language",
                        "Fifth strength is not shown",
                    ],
                    "missing_requirements": [
                        "Cloud platform unspecified",
                        "Domain knowledge unclear",
                        "Testing experience not explicit",
                        "Fourth gap is not shown",
                    ],
                    "risk_flags": [],
                    "role_summary": "Role summary",
                }
            ),
            "input_tokens": 10,
            "output_tokens": 10,
            "total_tokens": 20,
            "estimated_cost_usd": 0,
            "response_id": "",
            "score_status": score_status,
            "score_error": "" if score_status == "success" else "failed",
            "created_at": "2026-06-23T09:00:00+00:00",
        }
    )
    if draft_ready:
        repository.save_application_draft(
            {
                "draft_cache_key": f"draft-{source_job_id}",
                "score_cache_key": score_key,
                "semantic_key": item.semantic_key,
                "canonical_dedupe_key": item.dedupe_key,
                "prompt_version": DRAFT_PROMPT_VERSION,
                "model": "test-model",
                "result_json": json.dumps(
                    {
                        "cover_letter_language": "de",
                        "cover_letter": f"Cover letter for {source_job_id}",
                        "recruiter_message": "Recruiter message",
                    }
                ),
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0,
                "response_id": "",
                "draft_status": "ready",
                "draft_error": "",
                "rewrite_attempted": 0,
                "created_at": "2026-06-23T09:01:00+00:00",
            }
        )
    return item.semantic_key


def test_telegram_candidates_filter_and_order(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    try:
        seed_queue_item(repository, "apply-70", "apply", 70)
        seed_queue_item(repository, "strong-80", "strong_apply", 80)
        seed_queue_item(repository, "review-60", "review", 60)
        seed_queue_item(repository, "review-49", "review", 49)
        seed_queue_item(repository, "skip-99", "skip", 99)
        seed_queue_item(repository, "expired", "strong_apply", 90, expired=True)
        seed_queue_item(
            repository, "failed", "strong_apply", 95, score_status="failed"
        )

        rows = telegram_candidates(repository)
    finally:
        repository.close()

    assert [row["recommendation"] for row in rows] == [
        "strong_apply",
        "apply",
        "review",
    ]
    assert [row["match_score"] for row in rows] == [80, 70, 60]


def test_card_is_length_safe_and_has_expected_buttons(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    try:
        seed_queue_item(repository, "card", "strong_apply", 86)
        row = telegram_candidates(repository)[0]
    finally:
        repository.close()

    card = render_card(row)
    keyboard = card_keyboard(row)
    assert len(card) <= 3900
    assert "🔥 86% — STRONG APPLY" in card
    assert card.count("•") == 7
    assert "Description: Full" in card
    assert "Language: German" in card
    assert keyboard["inline_keyboard"][0][0]["text"] == "Open vacancy"
    assert keyboard["inline_keyboard"][0][1]["text"] == "Cover letter"


def test_send_does_not_resend_without_force(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    transport = FakeTelegram()
    try:
        semantic_key = seed_queue_item(
            repository, "delivery", "strong_apply", 90
        )
        first = send_cards(repository, transport, "chat", limit=1)
        second = send_cards(repository, transport, "chat", limit=1)
        forced = send_cards(
            repository, transport, "chat", limit=1, force=True
        )
        delivery = repository.telegram_delivery(semantic_key)
    finally:
        repository.close()

    assert first.sent == 1
    assert second.skipped_delivered == 1
    assert forced.sent == 1
    assert len(transport.sent) == 2
    assert delivery["message_id"] == 102
    assert delivery["delivered_at"]


def test_cover_applied_and_skip_callbacks_are_idempotent(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    transport = FakeTelegram()
    try:
        semantic_key = seed_queue_item(
            repository, "callbacks", "apply", 75
        )
        send_cards(repository, transport, "42", limit=1)
        base = {
            "message": {"chat": {"id": 42}, "message_id": 101},
        }
        process_callback(
            repository,
            transport,
            "42",
            {**base, "id": "cover-1", "data": f"cover:{semantic_key}"},
        )
        process_callback(
            repository,
            transport,
            "42",
            {**base, "id": "apply-1", "data": f"applied:{semantic_key}"},
        )
        first_applied_at = repository.telegram_delivery(semantic_key)["applied_at"]
        process_callback(
            repository,
            transport,
            "42",
            {**base, "id": "apply-2", "data": f"applied:{semantic_key}"},
        )
        second_applied_at = repository.telegram_delivery(semantic_key)["applied_at"]
        process_callback(
            repository,
            transport,
            "42",
            {**base, "id": "skip-1", "data": f"skipped:{semantic_key}"},
        )
        delivery = repository.telegram_delivery(semantic_key)
    finally:
        repository.close()

    assert transport.sent[1][1] == "Cover letter for callbacks"
    assert first_applied_at == second_applied_at
    assert "✅ APPLIED" in transport.edited[0][2]
    assert delivery["delivery_status"] == "skipped"
    assert delivery["skipped_at"]
    assert delivery["applied_at"] == ""
    assert "⏭ SKIPPED" in transport.edited[-1][2]


def test_process_updates_persists_offset(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    transport = FakeTelegram()
    try:
        semantic_key = seed_queue_item(repository, "update", "apply", 70)
        send_cards(repository, transport, "42", limit=1)
        offset = process_updates(
            repository,
            transport,
            "42",
            [
                {
                    "update_id": 15,
                    "callback_query": {
                        "id": "skip-update",
                        "data": f"skipped:{semantic_key}",
                        "message": {"chat": {"id": 42}, "message_id": 101},
                    },
                }
            ],
        )
        stored = repository.telegram_state("last_update_id")
    finally:
        repository.close()

    assert offset == 16
    assert stored == "15"


def test_preview_requires_no_credentials(tmp_path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")
    try:
        seed_queue_item(repository, "preview", "apply", 70)
        cards = preview_cards(repository, limit=1)
    finally:
        repository.close()

    assert len(cards) == 1
    assert "Role preview" in cards[0]
