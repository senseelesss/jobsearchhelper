"""Exercise the actual poll/ACK/serialized-consumer coordinator without network."""

import asyncio
import io
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from job_radar.control.runtime import serve_updates
from job_radar.control.telemetry import snapshot
from job_radar.telegram import TelegramApiClient, TelegramRateLimited


def update(number):
    return {"update_id": number, "callback_query": {"id": str(number)}}


def test_next_callback_ack_during_slow_handler_and_offsets_are_durable():
    started, release, acknowledged = threading.Event(), threading.Event(), threading.Event()
    handled, committed, offsets = [], [], []

    class Center:
        def __init__(self):
            self.repository = SimpleNamespace(set_telegram_state=lambda key, value: committed.append(int(value)))

        def acknowledge(self, item):
            if item["update_id"] == 2:
                acknowledged.set()

        def handle(self, item, **kwargs):
            assert kwargs["acknowledged"]
            if item["update_id"] == 1:
                started.set()
                assert release.wait(3)
            handled.append(item["update_id"])

        def search_notifications(self):
            pass

    class Transport:
        def get_updates(self, offset, timeout):
            offsets.append(offset)
            if not started.is_set():
                return [update(1)]
            time.sleep(.01)
            return [u for u in [update(1), update(2)] if offset is None or u["update_id"] >= offset]

    async def scenario():
        with ThreadPoolExecutor(max_workers=1) as executor:
            task = asyncio.create_task(serve_updates(Center(), Transport(), executor))
            try:
                assert await asyncio.to_thread(acknowledged.wait, 2)
                assert committed == []
                # Telegram must not be told to discard queued, uncommitted updates.
                assert set(offsets) == {None}
                release.set()
                for _ in range(100):
                    if committed == [1, 2]:
                        break
                    await asyncio.sleep(.01)
                assert handled == [1, 2]
                assert committed == [1, 2]
            finally:
                release.set()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
    asyncio.run(scenario())
    assert snapshot()["callback_ack_complete"]["count"] >= 2


def test_failed_durable_offset_stops_poller_without_acknowledging_server():
    offsets = []
    def fail(*args):
        raise OSError("disk full")
    center = SimpleNamespace(
        repository=SimpleNamespace(set_telegram_state=fail),
        acknowledge=lambda item: None,
        handle=lambda item, **kwargs: None,
        search_notifications=lambda: None,
    )
    def get_updates(offset, timeout):
        offsets.append(offset)
        return [update(7)]
    async def scenario():
        with ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises(OSError):
                await asyncio.wait_for(serve_updates(center, SimpleNamespace(get_updates=get_updates), executor), 2)
    asyncio.run(scenario())
    assert set(offsets) == {None}


def test_telegram_429_is_definite_and_preserves_retry_after(monkeypatch):
    def limited(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "limited", {}, io.BytesIO(
            b'{"parameters":{"retry_after":17}}'))
    monkeypatch.setattr("urllib.request.urlopen", limited)
    with pytest.raises(TelegramRateLimited) as result:
        TelegramApiClient("not-a-token").edit_message("42", 1, "private text")
    assert result.value.retry_after == 17
    assert "private" not in str(result.value)


def test_ack_has_short_network_timeout(monkeypatch):
    calls = []
    client = TelegramApiClient("not-a-token")
    monkeypatch.setattr(client, "_call", lambda method, payload, timeout: calls.append((method, timeout)))
    client.answer_callback("123")
    assert calls == [("answerCallbackQuery", 3)]


def test_rate_limit_cooldown_prevents_repeat_http_calls(monkeypatch):
    requests = []
    def limited(request, timeout):
        requests.append(request)
        raise urllib.error.HTTPError(request.full_url, 429, "limited", {}, io.BytesIO(
            b'{"parameters":{"retry_after":17}}'))
    monkeypatch.setattr("urllib.request.urlopen", limited)
    client = TelegramApiClient("not-a-token")
    for _ in range(3):
        with pytest.raises(TelegramRateLimited):
            client.edit_message("42", 1, "private text")
    assert len(requests) == 1
