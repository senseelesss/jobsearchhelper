"""One authorized async poller with serialized database work off the event loop."""

import asyncio
import hashlib
import logging
import os
import time
from functools import partial

import yaml
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from job_radar.control.telemetry import record
from job_radar.control.jobs import JobQuery
from job_radar.control.search import SearchConfig, SearchService
from job_radar.control.state import ControlStore
from job_radar.control.ui import ControlCenter
from job_radar.runner import RunLock
from job_radar.storage import JobRepository
from job_radar.telegram import TelegramApiClient

logger = logging.getLogger(__name__)


async def poll(settings, args):
    try:
        owner = (
            args.owner_id if args.owner_id is not None else int(os.getenv("TELEGRAM_OWNER_ID", "0"))
        )
    except ValueError:
        raise ValueError("TELEGRAM_OWNER_ID must be numeric") from None
    if not settings.telegram_bot_token or owner <= 0 or str(owner) != settings.telegram_chat_id:
        raise ValueError(
            "Set TELEGRAM_BOT_TOKEN, numeric TELEGRAM_OWNER_ID and matching private TELEGRAM_CHAT_ID"
        )
    database = (Path(args.database) if args.database else settings.database_path).resolve()
    transport = TelegramApiClient(settings.telegram_bot_token)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="telegram-db")
    loop = asyncio.get_running_loop()

    def construct():
        from job_radar.operations import health
        preflight = health(settings, database, Path(args.searches), Path(args.output), require_telegram=True, owner_id=owner)
        if preflight["errors"]:
            raise ValueError("Preflight failed: " + ", ".join(preflight["errors"]))
        profile = None
        try:
            from job_radar.candidate import load_candidate_profile

            profile = load_candidate_profile(settings.candidate_profile_path)
        except (ValueError, OSError, yaml.YAMLError):
            pass
        repository = JobRepository(database)
        store = ControlStore(database)
        jobs = JobQuery(repository, store, profile, settings.openai_model)
        from job_radar.ai.budget import request_ceiling
        try:
            ceiling = request_ceiling(settings)
        except ValueError:
            ceiling = 0
        config = SearchConfig(
            database,
            Path(args.searches).resolve(),
            Path(args.output).resolve(),
            cooldown=args.cooldown,
            timeout=args.search_timeout,
            limit=args.search_limit,
            max_pages=args.max_pages,
            transport=args.transport,
            enrichment_limit=getattr(args, "enrich_limit", 3),
            score_limit=getattr(args, "score_limit", 2),
            run_budget_usd=settings.ai_run_budget_usd,
            day_budget_usd=settings.ai_day_budget_usd,
            request_cost_usd=ceiling,
            ai_ready=bool(settings.openai_api_key and profile and any(profile.get(k) for k in ("education", "projects", "experience"))),
            model=settings.openai_model,
        )
        center = ControlCenter(
            jobs, SearchService(store, config), transport, owner, settings.telegram_chat_id
        )
        # Persist default locale so digest localization opts in when the center is first run.
        if store.get("language") is None:
            store.set("language", "uk")
        return center

    center = None
    # Token-derived lock prevents the same bot polling different DBs on this host.
    lock_name = hashlib.sha256(settings.telegram_bot_token.encode()).hexdigest()[:24]
    lock_dir = Path.home() / ".cache" / "job-radar" / "telegram-locks"
    try:
        with (
            RunLock(lock_dir / (lock_name + ".lock")),
            RunLock(database.with_suffix(database.suffix + ".telegram-poll.lock")),
        ):
            center = await loop.run_in_executor(executor, construct)
            previous = await loop.run_in_executor(
                executor, center.repository.telegram_state, "last_update_id"
            )
            offset = int(previous) + 1 if previous else None
            await serve_updates(center, transport, executor, offset)
    finally:
        if center:
            await loop.run_in_executor(executor, center.search.shutdown)
            await loop.run_in_executor(executor, center.store.close)
            await loop.run_in_executor(executor, center.repository.close)
        executor.shutdown(wait=True)


async def serve_updates(center, transport, executor, offset=None):
    """Poll/ack independently; serialize all SQLite, routing and durable offsets.

    Bounded memory and committed offsets retain Telegram's replay-on-restart model.
    Acknowledgement is UI feedback, never proof that an action has been committed.
    """
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue(maxsize=100)
    committed_offset = offset
    ack_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="telegram-ack")

    async def receive():
        nonlocal offset
        backoff = 1
        while True:
            try:
                updates = await asyncio.to_thread(transport.get_updates, committed_offset, 25)
                received = time.perf_counter()
                pending = []
                for update in updates:
                    update_id = int(update["update_id"])
                    if offset is not None and update_id < offset:
                        continue
                    pending.append(update)
                async def acknowledge(update):
                    if update.get("callback_query"):
                        record("callback_ack_dispatch", time.perf_counter() - received)
                        await loop.run_in_executor(ack_executor, center.acknowledge, update)
                        record("callback_ack_complete", time.perf_counter() - received)
                await asyncio.gather(*(acknowledge(update) for update in pending))
                for update in pending:
                    await queue.put((update, received))
                    offset = int(update["update_id"]) + 1
                if not pending:
                    await asyncio.sleep(0.25)
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("telegram_poll_retry", extra={"error_type": type(error).__name__})
                await asyncio.sleep(max(backoff, getattr(error, "retry_after", 0)))
                backoff = min(30, backoff * 2)

    async def consume():
        nonlocal committed_offset
        while True:
            try:
                update, received = await asyncio.wait_for(queue.get(), timeout=2)
            except asyncio.TimeoutError:
                try:
                    await loop.run_in_executor(executor, center.search_notifications)
                except Exception as error:
                    logger.warning("telegram_notification_failed", extra={"error_type": type(error).__name__})
                continue
            record("handler_queue_wait", time.perf_counter() - received)
            await loop.run_in_executor(
                executor, partial(center.handle, update, acknowledged=True, received_at=received)
            )
            # A failed durable write stops the consumer/poller; restart replays safely.
            await loop.run_in_executor(
                executor, center.repository.set_telegram_state,
                "last_update_id", str(update["update_id"]),
            )
            committed_offset = int(update["update_id"]) + 1
            queue.task_done()

    tasks = [asyncio.create_task(receive()), asyncio.create_task(consume())]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        ack_executor.shutdown(wait=True)
