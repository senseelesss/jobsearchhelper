"""One authorized async poller with serialized database work off the event loop."""

import asyncio
import hashlib
import logging
import os

import yaml
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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
        profile = None
        try:
            from job_radar.candidate import load_candidate_profile

            profile = load_candidate_profile(settings.candidate_profile_path)
        except (ValueError, OSError, yaml.YAMLError):
            pass
        repository = JobRepository(database)
        store = ControlStore(database)
        jobs = JobQuery(repository, store, profile, settings.openai_model)
        config = SearchConfig(
            database,
            Path(args.searches).resolve(),
            Path(args.output).resolve(),
            cooldown=args.cooldown,
            timeout=args.search_timeout,
            limit=args.search_limit,
            max_pages=args.max_pages,
            transport=args.transport,
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
            backoff = 1
            while True:
                try:
                    updates = await asyncio.to_thread(transport.get_updates, offset, 25)
                    for update in updates:
                        update_id = int(update["update_id"])
                        if offset is not None and update_id < offset:
                            continue
                        await loop.run_in_executor(executor, center.handle, update)
                        await loop.run_in_executor(
                            executor,
                            center.repository.set_telegram_state,
                            "last_update_id",
                            str(update_id),
                        )
                        offset = update_id + 1
                    backoff = 1
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.warning(
                        "telegram_poll_retry", extra={"error_type": type(error).__name__}
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(30, backoff * 2)
    finally:
        if center:
            await loop.run_in_executor(executor, center.store.close)
            await loop.run_in_executor(executor, center.repository.close)
        executor.shutdown(wait=True)
