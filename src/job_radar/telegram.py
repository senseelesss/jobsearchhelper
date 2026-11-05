import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from job_radar.storage import JobRepository

TELEGRAM_MESSAGE_LIMIT = 4096
CARD_LIMIT = 3900
RECOMMENDATION_ORDER = {"strong_apply": 0, "apply": 1, "review": 2}


def application_queue(*args, **kwargs):
    from job_radar.matching import application_queue as build_queue

    return build_queue(*args, **kwargs)


class TelegramTransport(Protocol):
    def send_message(
        self,
        chat_id: str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> int: ...

    def edit_message(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None: ...

    def answer_callback(self, callback_query_id: str, text: str = "") -> None: ...

    def get_updates(
        self, offset: int | None = None, timeout: int = 25
    ) -> list[dict[str, Any]]: ...


class TelegramApiClient:
    def __init__(self, bot_token: str) -> None:
        self._base_url = f"https://api.telegram.org/bot{bot_token}"

    def send_message(
        self,
        chat_id: str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> int:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        result = self._call("sendMessage", payload)
        return int(result["message_id"])

    def edit_message(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self._call("editMessageText", payload)

    def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        self._call(
            "answerCallbackQuery",
            {"callback_query_id": callback_query_id, "text": text},
        )

    def get_updates(
        self, offset: int | None = None, timeout: int = 25
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload, timeout=timeout + 5)
        return list(result)

    def _call(
        self,
        method: str,
        payload: dict[str, Any],
        timeout: int = 30,
    ) -> Any:
        request = urllib.request.Request(
            f"{self._base_url}/{method}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raise RuntimeError(
                f"Telegram API HTTP {error.code} during {method}"
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RuntimeError(
                f"Telegram API transport failure during {method}: "
                f"{type(error).__name__}"
            ) from None
        if not result.get("ok"):
            raise RuntimeError(f"Telegram API rejected {method}")
        return result["result"]


@dataclass(frozen=True, slots=True)
class DeliverySummary:
    sent: int
    skipped_delivered: int
    failed: int = 0


def telegram_candidates(
    repository: JobRepository,
    profile_hash: str | None = None,
    model: str | None = None,
    semantic_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    rows = []
    for row in application_queue(
        repository,
        all_active=True,
        profile_hash=profile_hash,
        model=model,
    ):
        if semantic_keys is not None and row["semantic_key"] not in semantic_keys:
            continue
        recommendation = row["recommendation"]
        score = row["match_score"]
        if row["vacancy_state"] != "active":
            continue
        if row["score_status"] != "success":
            continue
        if row["draft_status"] != "ready":
            continue
        if recommendation in {"strong_apply", "apply"}:
            rows.append(row)
        elif recommendation == "review" and score is not None and score >= 50:
            rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            RECOMMENDATION_ORDER[row["recommendation"]],
            -int(row["match_score"]),
        ),
    )


def render_card(row: dict[str, Any], status: str = "delivered") -> str:
    recommendation = str(row["recommendation"])
    label = recommendation.replace("_", " ").upper()
    icon = "🔥" if recommendation == "strong_apply" else "✅"
    location, work_format = _location_parts(str(row["location"]))
    company_line = " | ".join(
        value for value in (str(row["company"]), location, work_format) if value
    )
    strengths = [
        f"• {_concise(value)}"
        for value in list(row["matching_strengths"])[:4]
    ] or ["• No concise strengths available"]
    gaps = [
        f"• {_concise(value)}"
        for value in list(row["missing_requirements"])[:3]
    ] or ["• No explicit gaps identified"]
    confidence = str(row["score_confidence"]).title()
    description = (
        "Full"
        if row["description_confidence"] in {"high", "medium"}
        and not row["provisional"]
        else "Snippet"
    )
    language = {
        "de": "German",
        "en": "English",
    }.get(str(row["description_language"]), "Unknown")
    lines = [
        f"{icon} {row['match_score']}% — {label}",
        str(row["title"]),
        company_line,
        "",
        "Why it fits:",
        *strengths,
        "",
        "Gaps:",
        *gaps,
        "",
        f"Confidence: {confidence}",
        f"Description: {description}",
        f"Language: {language}",
    ]
    if status == "applied":
        lines.extend(["", "✅ APPLIED"])
    elif status == "skipped":
        lines.extend(["", "⏭ SKIPPED"])
    return _truncate("\n".join(lines), CARD_LIMIT)


def card_keyboard(row: dict[str, Any]) -> dict[str, Any]:
    semantic_key = str(row["semantic_key"])
    return {
        "inline_keyboard": [
            [
                {"text": "Open vacancy", "url": row["url"]},
                {
                    "text": "Cover letter",
                    "callback_data": f"cover:{semantic_key}",
                },
            ],
            [
                {"text": "Applied", "callback_data": f"applied:{semantic_key}"},
                {"text": "Skip", "callback_data": f"skipped:{semantic_key}"},
            ],
        ]
    }


def preview_cards(
    repository: JobRepository,
    limit: int | None = None,
    profile_hash: str | None = None,
    model: str | None = None,
) -> list[str]:
    rows = telegram_candidates(repository, profile_hash, model)
    if limit is not None:
        rows = rows[:limit]
    return [render_card(row) for row in rows]


def send_cards(
    repository: JobRepository,
    transport: TelegramTransport,
    chat_id: str,
    limit: int | None = None,
    force: bool = False,
    profile_hash: str | None = None,
    model: str | None = None,
    semantic_keys: set[str] | None = None,
) -> DeliverySummary:
    rows = telegram_candidates(
        repository,
        profile_hash,
        model,
        semantic_keys,
    )
    if limit is not None:
        rows = rows[:limit]
    sent = skipped_delivered = failed = 0
    for row in rows:
        existing = repository.telegram_delivery(row["semantic_key"])
        if (
            existing
            and existing["delivery_status"] in {"delivered", "applied", "skipped"}
            and not force
        ):
            skipped_delivered += 1
            continue
        try:
            message_id = transport.send_message(
                chat_id,
                render_card(row),
                card_keyboard(row),
            )
            repository.save_telegram_delivery(
                row["semantic_key"], message_id, _now()
            )
            sent += 1
        except Exception:
            failed += 1
    return DeliverySummary(sent, skipped_delivered, failed)


def process_callback(
    repository: JobRepository,
    transport: TelegramTransport,
    chat_id: str,
    callback: dict[str, Any],
    profile_hash: str | None = None,
    model: str | None = None,
) -> None:
    callback_id = str(callback.get("id", ""))
    data = str(callback.get("data", ""))
    message = callback.get("message") or {}
    callback_chat_id = str((message.get("chat") or {}).get("id", ""))
    if callback_chat_id != str(chat_id):
        transport.answer_callback(callback_id, "Ignored")
        return
    direct = re.fullmatch(r"(applyjob|skipjob):((?:linkedin|karriere|indeed):[A-Za-z0-9_-]+)", data)
    if direct:
        state = "applied" if direct.group(1) == "applyjob" else "skipped"
        try:
            repository.set_application(direct.group(2), state, f"telegram:{callback_id}", actor="telegram")
            transport.answer_callback(callback_id, "Applied" if state == "applied" else "Skipped")
        except ValueError:
            transport.answer_callback(callback_id, "Vacancy unavailable")
        return
    match = re.fullmatch(r"(cover|applied|skipped):(semantic-[a-f0-9]+)", data)
    if not match:
        transport.answer_callback(callback_id, "Unknown action")
        return
    action, semantic_key = match.groups()
    row = next(
        (
            item
            for item in application_queue(
                repository,
                all_active=True,
                profile_hash=profile_hash,
                model=model,
            )
            if item["semantic_key"] == semantic_key
        ),
        None,
    )
    delivery = repository.telegram_delivery(semantic_key)
    if not row or not delivery:
        transport.answer_callback(callback_id, "Vacancy unavailable")
        return

    if action == "cover":
        cover_letter = str(row["cover_letter"]).strip()
        if not cover_letter:
            transport.answer_callback(callback_id, "Cover letter unavailable")
            return
        transport.send_message(
            chat_id,
            _truncate(cover_letter, TELEGRAM_MESSAGE_LIMIT),
        )
        transport.answer_callback(callback_id, "Cover letter sent")
        return

    updated = repository.update_telegram_status(
        semantic_key, action, _now()
    )
    if delivery.get("canonical_dedupe_key"):
        repository.set_application(
            delivery["canonical_dedupe_key"], action,
            f"telegram:{callback_id}", actor="telegram",
        )
    transport.edit_message(
        chat_id,
        int(delivery["message_id"]),
        render_card(row, action),
        card_keyboard(row),
    )
    label = "Applied" if action == "applied" else "Skipped"
    transport.answer_callback(
        callback_id,
        f"{label}" if updated else "Vacancy unavailable",
    )


def poll_callbacks(
    repository: JobRepository,
    transport: TelegramTransport,
    chat_id: str,
    profile_hash: str | None = None,
    model: str | None = None,
) -> None:
    last_update = repository.telegram_state("last_update_id")
    offset = int(last_update) + 1 if last_update else None
    while True:
        updates = transport.get_updates(offset=offset)
        offset = process_updates(
            repository,
            transport,
            chat_id,
            updates,
            offset,
            profile_hash,
            model,
        )


def process_updates(
    repository: JobRepository,
    transport: TelegramTransport,
    chat_id: str,
    updates: list[dict[str, Any]],
    offset: int | None = None,
    profile_hash: str | None = None,
    model: str | None = None,
) -> int | None:
    for update in updates:
        update_id = int(update["update_id"])
        callback = update.get("callback_query")
        if callback:
            process_callback(
                repository,
                transport,
                chat_id,
                callback,
                profile_hash,
                model,
            )
        repository.set_telegram_state("last_update_id", str(update_id))
        offset = update_id + 1
    return offset


def _location_parts(value: str) -> tuple[str, str]:
    match = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", value)
    if not match:
        return value, ""
    location = re.sub(r",\s*Austria$", "", match.group(1)).strip()
    return location, match.group(2).strip()


def _concise(value: str, limit: int = 220) -> str:
    return _truncate(re.sub(r"\s+", " ", value).strip(), limit)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
