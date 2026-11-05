import argparse
import csv
import json
from pathlib import Path

from job_radar.config import Settings
from job_radar.exporting import collapse_semantic
from job_radar.storage import JobRepository


def _scrape(args: argparse.Namespace) -> None:
    from job_radar.http import HttpClient
    from job_radar.sources.browser import PlaywrightClient
    from job_radar.sources import karriere, linkedin
    from job_radar.collection import collect_search
    from job_radar.searches import validate_search_url

    client = (
        PlaywrightClient(timeout_seconds=20, headed=args.headed)
        if args.transport == "playwright"
        else HttpClient(timeout_seconds=20, rate_limit_seconds=2, retries=0)
    )
    source = args.source or (validate_search_url(args.url).source if args.url else "karriere")
    default_locations = {"karriere": "wien", "linkedin": "Vienna, Austria"}
    builders = {"karriere": karriere.search_url, "linkedin": linkedin.search_url}
    url = args.url or builders[source](args.query, args.location or default_locations[source])
    if validate_search_url(url).source != source:
        raise ValueError("--source does not match --url")
    result = collect_search(client, url, limit=args.limit, max_pages=args.pages, details=args.details)
    output = Path(args.output or f"outputs/{source}-probe/jobs.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {**result.to_dict(), "transport": args.transport},
            ensure_ascii=False, indent=2,
        ), encoding="utf-8"
    )
    print(
        f"Scrape {source}: status={result.status}; jobs={len(result.jobs)}; "
        f"cards={result.cards_seen}; invalid={result.invalid_cards}; "
        f"pages={result.pages_fetched}; stop={result.stop_reason}; "
        f"coverage_complete={str(result.coverage_complete).lower()}; output={output}"
    )
    if result.error:
        print(result.error)
        if args.transport == "playwright" and result.http_status is None:
            print(
                'Check installation: pip install -e ".[scraping]"; '
                "python -m playwright install chromium; or try --transport http."
            )
    if result.status not in {"success", "empty"}:
        raise SystemExit(1)


def _search_add(args):
    import yaml
    from job_radar.searches import validate_search_url, load_searches
    path = Path(args.file)
    search = validate_search_url(args.url, args.name)
    data = yaml.safe_load(path.read_text()) if path.exists() else {"searches": []}
    if not isinstance(data, dict) or not isinstance(data.get("searches"), list):
        raise ValueError("Invalid existing search configuration")
    if any(item.get("name") == search.name for item in data["searches"]):
        raise ValueError("Search name already exists; edit the YAML to change it")
    candidate = {"searches": data["searches"] + [{"name":search.name, "url":search.url, "enabled":True}]}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pending.yaml")
    try:
        temporary.write_text(yaml.safe_dump(candidate, allow_unicode=True, sort_keys=False))
        load_searches(temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Saved search {search.name} ({search.source}) in {path}")


def _telegram_connect(args, settings):
    import getpass
    import os
    from dotenv import set_key
    from job_radar.telegram import TelegramApiClient
    token = settings.telegram_bot_token
    chat = settings.telegram_chat_id
    if args.command == "telegram-connect":
        token = token or getpass.getpass("BotFather bot token (hidden): ").strip()
        chat = chat or getpass.getpass("Your Telegram chat ID (hidden): ").strip()
    if not token or not chat:
        raise ValueError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, or run telegram-connect")
    client = TelegramApiClient(token)
    bot = client._call("getMe", {}, timeout=15)
    client._call("getChat", {"chat_id":chat}, timeout=15)
    if not bot.get("is_bot"):
        raise ValueError("Configured token is not a bot")
    if args.command == "telegram-connect":
        path = Path(".env")
        path.touch(mode=0o600, exist_ok=True)
        os.chmod(path, 0o600)
        set_key(str(path), "TELEGRAM_BOT_TOKEN", token)
        set_key(str(path), "TELEGRAM_CHAT_ID", chat)
        print("Telegram connected; settings saved privately in .env. Shell variables take precedence.")
    else:
        print("Telegram bot and chat access verified.")
    if args.test_message:
        client.send_message(chat, "Job Radar: Telegram connection test succeeded.")
        print("Test message sent.")


def _daily(args, settings):
    from job_radar.collection import collect_search
    from job_radar.daily import daily_cycle
    from job_radar.searches import load_searches
    from job_radar.http import HttpClient
    from job_radar.sources.browser import PlaywrightClient
    from job_radar.telegram import TelegramApiClient
    searches = load_searches(Path(args.searches))
    client = PlaywrightClient() if args.transport == "playwright" else HttpClient(15, 2, 1)
    def collector(url):
        return collect_search(client, url, limit=args.limit, max_pages=args.pages, details=args.details)
    def email_fetcher():
        from job_radar.gmail import gmail_service, fetch_messages
        if not settings.token_path.is_file():
            raise RuntimeError("Authorize Gmail interactively with backfill before daily runs")
        service = gmail_service(settings.credentials_path, settings.token_path)
        return fetch_messages(service, "newer_than:10d (from:jobalerts-noreply@linkedin.com OR from:donotreply@jobalert.indeed.com)", limit=args.email_limit)
    provider = None
    profile = None
    if args.score_limit:
        from job_radar.candidate import load_candidate_profile
        from job_radar.ai import OpenAIProvider
        profile = load_candidate_profile(settings.candidate_profile_path)
        if settings.openai_api_key:
            provider = OpenAIProvider(settings.openai_api_key, settings.openai_model)
    if args.telegram and (not settings.telegram_bot_token or not settings.telegram_chat_id):
        raise ValueError("Run telegram-connect before enabling daily delivery")
    result = daily_cycle(
        Path(args.database), searches, collector,
        fetch_emails=None if args.no_email else email_fetcher,
        provider=provider, profile=profile, score_limit=args.score_limit,
        telegram=TelegramApiClient(settings.telegram_bot_token) if args.telegram else None,
        chat_id=settings.telegram_chat_id, output=Path(args.output),
        import_workbook=Path(args.import_xlsx) if args.import_xlsx else None,
        input_cost_per_million=settings.openai_input_cost_per_million,
        output_cost_per_million=settings.openai_output_cost_per_million,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "success":
        raise SystemExit(1)


def _application_set(args):
    from job_radar.runner import RunLock
    path = Path(args.database)
    with RunLock(path.with_suffix(path.suffix + ".run.lock")), JobRepository(path) as repository:
        result = repository.set_application(args.key, args.state, args.request_id, note=args.note)
    print(f"Application status: {result}")


def _backfill(args: argparse.Namespace, settings: Settings) -> None:
    from job_radar.gmail import fetch_messages, gmail_service
    from job_radar.pipeline import extract_jobs

    query = args.query or (
        f"newer_than:{args.days}d "
        "(from:jobalerts-noreply@linkedin.com "
        "OR from:donotreply@jobalert.indeed.com)"
    )
    service = gmail_service(settings.credentials_path, settings.token_path)
    messages = list(fetch_messages(service, query, args.limit))
    jobs = extract_jobs(messages)

    print(f"Fetched emails: {len(messages)}")
    print(f"Unique jobs: {len(jobs)}")

    if args.dry_run:
        for job in jobs:
            url = (
                "[sponsored link stored]"
                if job.source_job_id.startswith("sponsored-")
                else job.url
            )
            print(f"[{job.source}] {job.title} | {job.company} | {url}")
        return

    repository = JobRepository(settings.database_path)
    try:
        inserted, updated = repository.upsert(jobs)
    finally:
        repository.close()
    print(f"Inserted: {inserted}; updated: {updated}")


def _export(args: argparse.Namespace, settings: Settings) -> None:
    repository = JobRepository(settings.database_path)
    try:
        rows = repository.all(
            args.only,
            require_description=(
                args.require_description and not args.collapse_semantic
            ),
        )
    finally:
        repository.close()
    if args.collapse_semantic:
        rows = collapse_semantic(rows)
        if args.require_description:
            rows = [
                row
                for row in rows
                if row["fetch_status"] in {"success", "snippet_only"}
                and row["description"]
            ]

    output = Path(args.output or f"data/jobs.{args.format}")
    output.parent.mkdir(parents=True, exist_ok=True)

    if args.format == "json":
        output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        csv_rows = [
            {
                **row,
                "rejection_reasons": json.dumps(
                    row["rejection_reasons"], ensure_ascii=False
                ),
                **(
                    {
                        "alternate_source_urls": json.dumps(
                            row["alternate_source_urls"], ensure_ascii=False
                        )
                    }
                    if "alternate_source_urls" in row
                    else {}
                ),
            }
            for row in rows
        ]
        with output.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(
                file, fieldnames=list(csv_rows[0]) if csv_rows else []
            )
            if csv_rows:
                writer.writeheader()
                writer.writerows(csv_rows)
    print(f"Exported {len(rows)} jobs to {output}")


def _enrich(args: argparse.Namespace, settings: Settings) -> None:
    from job_radar.enrichment import enrich_jobs
    from job_radar.http import HttpClient

    repository = JobRepository(settings.database_path)
    try:
        totals = enrich_jobs(
            repository,
            HttpClient(
                timeout_seconds=settings.enrichment_timeout_seconds,
                rate_limit_seconds=settings.enrichment_rate_limit_seconds,
                retries=settings.enrichment_retries,
            ),
            only=args.only,
            retry_failed=args.retry_failed,
            refresh=getattr(args, "refresh", False),
        )
    finally:
        repository.close()

    print(
        f"Enrichment: success={totals['success']}; "
        f"snippet_only={totals['snippet_only']}; expired={totals['expired']}; "
        f"blocked={totals['blocked']}; empty={totals['empty']}; "
        f"failed={totals['failed']}"
    )


def _enrichment_report(args: argparse.Namespace, settings: Settings) -> None:
    from job_radar.enrichment import enrichment_report

    repository = JobRepository(settings.database_path)
    try:
        rows = enrichment_report(repository, args.only)
    finally:
        repository.close()

    print("source\ttitle\tfetch_status\tfetch_error\thttp_status\tfinal_url")
    for row in rows:
        final_url = str(row["final_url"])
        if "/pagead/" in final_url:
            final_url = "[sponsored link stored]"
        print(
            f"{row['source']}\t{row['title']}\t{row['fetch_status']}\t"
            f"{row['fetch_error']}\t{row['http_status'] or ''}\t{final_url}"
        )


def _score(args: argparse.Namespace, settings: Settings) -> None:
    from job_radar.ai import OpenAIProvider
    from job_radar.candidate import candidate_profile_hash, load_candidate_profile
    from job_radar.matching import (
        SCORE_PROMPT_VERSION,
        score_cache_key,
        score_jobs,
        scoring_candidates,
    )

    profile = load_candidate_profile(settings.candidate_profile_path)
    repository = JobRepository(settings.database_path)
    try:
        if args.dry_run:
            profile_hash = candidate_profile_hash(profile)
            only = None if args.all_active else args.only
            candidates = scoring_candidates(repository, only)
            if args.retry_failed:
                candidates = [
                    vacancy
                    for vacancy in candidates
                    if repository.failed_job_score(
                        str(vacancy["semantic_key"]),
                        profile_hash,
                        str(vacancy["description_hash"]),
                        SCORE_PROMPT_VERSION,
                        settings.openai_model,
                    )
                    and not repository.reusable_job_score(
                        str(vacancy["semantic_key"]),
                        profile_hash,
                        str(vacancy["description_hash"]),
                        SCORE_PROMPT_VERSION,
                        settings.openai_model,
                    )
                ]
            if args.limit is not None:
                candidates = candidates[: args.limit]
            for vacancy in candidates:
                cache_key = score_cache_key(
                    str(vacancy["semantic_key"]),
                    profile_hash,
                    str(vacancy["description_hash"]),
                    SCORE_PROMPT_VERSION,
                    settings.openai_model,
                )
                cached = repository.cached_job_score(cache_key)
                reusable = repository.reusable_job_score(
                    str(vacancy["semantic_key"]),
                    profile_hash,
                    str(vacancy["description_hash"]),
                    SCORE_PROMPT_VERSION,
                    settings.openai_model,
                )
                confidence = (
                    "provisional"
                    if vacancy["provisional"]
                    else "final"
                )
                print(
                    f"[{vacancy['screening_status']}] {vacancy['title']} | "
                    f"{vacancy['company']} | {confidence} | "
                    f"cache={'hit' if (cached and cached['score_status'] == 'success') or reusable else 'miss'}"
                )
            print(f"Dry run candidates: {len(candidates)}")
            return

        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for scoring")
        summary = score_jobs(
            repository,
            OpenAIProvider(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
            ),
            profile,
            only=None if args.all_active else args.only,
            limit=args.limit,
            retry_failed=args.retry_failed,
            input_cost_per_million=settings.openai_input_cost_per_million,
            output_cost_per_million=settings.openai_output_cost_per_million,
        )
    finally:
        repository.close()

    print(
        f"Scoring: scored={summary.scored}; cached={summary.cached}; "
        f"failed={summary.failed}; skipped={summary.skipped}; "
        f"estimated_cost_usd={summary.estimated_cost_usd:.6f}"
    )


def _draft(args: argparse.Namespace, settings: Settings) -> None:
    from job_radar.ai import OpenAIProvider
    from job_radar.candidate import load_candidate_profile
    from job_radar.matching import draft_jobs

    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for draft generation")
    recommendations = {
        value.strip()
        for value in args.recommendation.split(",")
        if value.strip()
    }
    allowed = {"strong_apply", "apply", "review", "skip"}
    if not recommendations or not recommendations <= allowed:
        raise ValueError(
            "--recommendation must contain comma-separated values from "
            "strong_apply,apply,review,skip"
        )

    repository = JobRepository(settings.database_path)
    try:
        summary = draft_jobs(
            repository,
            OpenAIProvider(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
            ),
            load_candidate_profile(settings.candidate_profile_path),
            recommendations=recommendations,
            retry_failed=args.retry_failed,
            limit=args.limit,
            input_cost_per_million=settings.openai_input_cost_per_million,
            output_cost_per_million=settings.openai_output_cost_per_million,
        )
    finally:
        repository.close()

    print(
        f"Drafting: ready={summary.ready}; cached={summary.cached}; "
        f"failed={summary.failed}; not_applicable={summary.not_applicable}; "
        f"estimated_cost_usd={summary.estimated_cost_usd:.6f}"
    )


def _application_report(args: argparse.Namespace, settings: Settings) -> None:
    from job_radar.candidate import candidate_profile_hash, load_candidate_profile
    from job_radar.matching import application_queue

    profile_hash = candidate_profile_hash(
        load_candidate_profile(settings.candidate_profile_path)
    )
    repository = JobRepository(settings.database_path)
    try:
        rows = application_queue(
            repository,
            all_active=args.all_active,
            profile_hash=profile_hash,
            model=settings.openai_model,
        )
    finally:
        repository.close()

    if args.format == "json":
        output = Path(args.output or "data/application_queue.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Exported {len(rows)} application queue items to {output}")
        return

    print(
        "state\tscore_status\tdraft_status\tscore\trecommendation\t"
        "confidence\tprovisional\ttitle\tcompany\tmodel"
    )
    for row in rows:
        print(
            f"{row['vacancy_state']}\t{row['score_status']}\t"
            f"{row['draft_status']}\t{row.get('match_score', '')}\t"
            f"{row.get('recommendation', '')}\t"
            f"{row.get('score_confidence', '')}\t"
            f"{str(row['provisional']).lower()}\t"
            f"{row['title']}\t{row['company']}\t{row['model']}"
        )


def _funnel_report(settings: Settings) -> None:
    from job_radar.candidate import candidate_profile_hash, load_candidate_profile
    from job_radar.matching import funnel_counts

    profile_hash = candidate_profile_hash(
        load_candidate_profile(settings.candidate_profile_path)
    )
    repository = JobRepository(settings.database_path)
    try:
        counts = funnel_counts(
            repository,
            profile_hash=profile_hash,
            model=settings.openai_model,
        )
    finally:
        repository.close()

    for key in (
        "source_jobs",
        "semantic_vacancies",
        "eligible",
        "review",
        "rejected",
        "expired",
        "enriched_full",
        "snippet_only",
        "scored",
        "score_failed",
        "draft_ready",
        "draft_failed",
        "estimated_api_cost",
    ):
        print(f"{key}: {counts[key]}")


def _telegram_preview(args: argparse.Namespace, settings: Settings) -> None:
    from job_radar.candidate import candidate_profile_hash, load_candidate_profile
    from job_radar.telegram import preview_cards

    profile_hash = candidate_profile_hash(
        load_candidate_profile(settings.candidate_profile_path)
    )
    repository = JobRepository(settings.database_path)
    try:
        cards = preview_cards(
            repository,
            args.limit,
            profile_hash,
            settings.openai_model,
        )
    finally:
        repository.close()

    for index, card in enumerate(cards, start=1):
        if index > 1:
            print("\n" + "-" * 60 + "\n")
        print(card)
    print(f"\nPreviewed: {len(cards)}")


def _telegram_send(args: argparse.Namespace, settings: Settings) -> None:
    from job_radar.candidate import candidate_profile_hash, load_candidate_profile
    from job_radar.telegram import TelegramApiClient, send_cards

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required"
        )
    limit = None if args.all else args.limit
    profile_hash = candidate_profile_hash(
        load_candidate_profile(settings.candidate_profile_path)
    )
    repository = JobRepository(settings.database_path)
    try:
        summary = send_cards(
            repository,
            TelegramApiClient(settings.telegram_bot_token),
            settings.telegram_chat_id,
            limit=limit,
            force=args.force,
            profile_hash=profile_hash,
            model=settings.openai_model,
        )
    finally:
        repository.close()
    print(
        f"Telegram: sent={summary.sent}; "
        f"skipped_delivered={summary.skipped_delivered}; failed={summary.failed}"
    )


def _telegram_poll(settings: Settings) -> None:
    from job_radar.candidate import candidate_profile_hash, load_candidate_profile
    from job_radar.telegram import TelegramApiClient, poll_callbacks

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required"
        )
    repository = JobRepository(settings.database_path)
    profile_hash = candidate_profile_hash(
        load_candidate_profile(settings.candidate_profile_path)
    )
    try:
        poll_callbacks(
            repository,
            TelegramApiClient(settings.telegram_bot_token),
            settings.telegram_chat_id,
            profile_hash,
            settings.openai_model,
        )
    except KeyboardInterrupt:
        print("Telegram polling stopped")
    finally:
        repository.close()


def _screen(settings: Settings) -> None:
    repository = JobRepository(settings.database_path)
    try:
        counts = repository.screen_all()
    finally:
        repository.close()

    total = sum(counts.values())
    print(
        f"Screened: {total}; eligible: {counts['eligible']}; "
        f"review: {counts['review']}; rejected: {counts['rejected']}"
    )


def _stats(settings: Settings) -> None:
    repository = JobRepository(settings.database_path)
    try:
        rows = repository.stats()
    finally:
        repository.close()
    total = sum(int(row["count"]) for row in rows)
    print(f"Total: {total}")
    for row in rows:
        print(f"{row['source']}: {row['count']}")


def _run(args: argparse.Namespace, settings: Settings) -> None:
    from job_radar.ai import OpenAIProvider
    from job_radar.candidate import load_candidate_profile
    from job_radar.gmail import fetch_messages, gmail_service
    from job_radar.http import HttpClient
    from job_radar.runner import (
        RunAlreadyActiveError,
        RunDependencies,
        RunOptions,
        run_pipeline,
    )
    from job_radar.telegram import TelegramApiClient

    def gmail_fetcher(days: int):
        query = (
            f"newer_than:{days}d "
            "(from:jobalerts-noreply@linkedin.com "
            "OR from:donotreply@jobalert.indeed.com)"
        )
        service = gmail_service(settings.credentials_path, settings.token_path)
        return fetch_messages(service, query)

    provider = (
        OpenAIProvider(settings.openai_api_key, settings.openai_model)
        if settings.openai_api_key and not args.dry_run
        else None
    )
    telegram = (
        TelegramApiClient(settings.telegram_bot_token)
        if settings.telegram_bot_token
        and not args.no_telegram
        and not args.dry_run
        else None
    )
    dependencies = RunDependencies(
        fetch_messages=gmail_fetcher,
        http_client=HttpClient(
            timeout_seconds=settings.enrichment_timeout_seconds,
            rate_limit_seconds=settings.enrichment_rate_limit_seconds,
            retries=settings.enrichment_retries,
        ),
        profile=load_candidate_profile(settings.candidate_profile_path),
        ai_provider=provider,
        telegram_transport=telegram,
        telegram_chat_id=settings.telegram_chat_id,
        input_cost_per_million=settings.openai_input_cost_per_million,
        output_cost_per_million=settings.openai_output_cost_per_million,
    )
    try:
        report = run_pipeline(
            settings.database_path,
            RunOptions(args.days, args.dry_run, args.no_telegram),
            dependencies,
        )
    except RunAlreadyActiveError as error:
        raise SystemExit(str(error)) from error

    counts = report.counts
    print(
        f"Run {report.run_id} {report.status}: emails={counts['emails']}; "
        f"new={counts['inserted']}; changed={counts['changed']}; "
        f"screened={counts['screened']}; semantic={counts['semantic']}; "
        f"enriched={counts['enriched']}; scored={counts['scored']}; "
        f"score_cache_hits={counts['score_cache_hits']}; "
        f"drafts={counts['drafts']}; draft_cache_hits={counts['draft_cache_hits']}; "
        f"telegram={counts['telegram_sent']}; failed={counts['failed']}; "
        f"estimated_cost_usd={report.estimated_cost_usd:.6f}"
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="job-radar",
        description="Incremental email-first vacancy pipeline and application queue.",
    )
    commands = root.add_subparsers(dest="command", required=True)

    scrape = commands.add_parser("scrape", help="Probe one public search page into JSON")
    scrape.add_argument("--source", choices=("karriere", "linkedin"))
    scrape.add_argument("--url", help="Paste a public filtered search URL; source is detected")
    scrape.add_argument("--query", default="python")
    scrape.add_argument("--location", help="Default: wien / Vienna, Austria for LinkedIn")
    scrape.add_argument("--transport", choices=("playwright", "http"), default="playwright")
    scrape.add_argument("--limit", type=int, choices=range(1, 101), default=5, metavar="1..100")
    scrape.add_argument("--pages", type=int, choices=range(1,11), default=1, metavar="1..10")
    scrape.add_argument("--details", type=int, choices=range(26), default=0, metavar="0..25")
    scrape.add_argument("--headed", action="store_true", help="Show the Playwright browser")
    scrape.add_argument("--output", help="Default: outputs/<source>-probe/jobs.json")

    search_add = commands.add_parser("search-add", help="Save a filtered search URL")
    search_add.add_argument("url")
    search_add.add_argument("--name", required=True)
    search_add.add_argument("--file", default="data/searches.yaml")
    for name in ("telegram-connect", "telegram-check"):
        connection = commands.add_parser(name)
        connection.add_argument("--test-message", action="store_true")
    daily = commands.add_parser("daily", help="Collect saved searches and optional email into a test DB")
    daily.add_argument("--searches", default="data/searches.yaml")
    daily.add_argument("--database", default="data/mvp-v1-test.db")
    daily.add_argument("--output", default="outputs/daily")
    daily.add_argument("--transport", choices=("playwright", "http"), default="playwright")
    daily.add_argument("--pages", type=int, choices=range(1,11), default=2, metavar="1..10")
    daily.add_argument("--limit", type=int, choices=range(1,501), default=25, metavar="1..500")
    daily.add_argument("--details", type=int, choices=range(26), default=5, metavar="0..25")
    daily.add_argument("--email-limit", type=int, choices=range(1,1001), default=100, metavar="1..1000")
    daily.add_argument("--no-email", action="store_true")
    daily.add_argument("--score-limit", type=int, choices=range(26), default=0, metavar="0..25")
    daily.add_argument("--telegram", action="store_true", help="Send a daily digest to the configured chat")
    daily.add_argument("--import-xlsx", help="Import explicit status edits before collection")
    application = commands.add_parser("application-set")
    application.add_argument("key")
    application.add_argument("state")
    application.add_argument("--request-id", required=True)
    application.add_argument("--note", default="")
    application.add_argument("--database", default="data/mvp-v1-test.db")

    backfill = commands.add_parser("backfill")
    backfill.add_argument("--days", type=int, default=10)
    backfill.add_argument("--query")
    backfill.add_argument("--limit", type=int)
    backfill.add_argument("--dry-run", action="store_true")

    export = commands.add_parser("export")
    export.add_argument("--format", choices=("json", "csv"), default="json")
    export.add_argument("--output")
    export.add_argument("--only", choices=("eligible", "review", "rejected"))
    export.add_argument("--collapse-semantic", action="store_true")
    export.add_argument("--require-description", action="store_true")

    enrich = commands.add_parser("enrich")
    enrich.add_argument("--only", choices=("eligible", "review"))
    enrich.add_argument("--retry-failed", action="store_true")
    enrich.add_argument("--refresh", action="store_true")
    report = commands.add_parser("enrichment-report")
    report.add_argument("--only", choices=("eligible", "review"), default="eligible")
    score = commands.add_parser("score")
    score_scope = score.add_mutually_exclusive_group()
    score_scope.add_argument("--only", choices=("eligible", "review"))
    score_scope.add_argument("--all-active", action="store_true")
    score.add_argument("--retry-failed", action="store_true")
    score.add_argument("--limit", type=int)
    score.add_argument("--dry-run", action="store_true")
    draft = commands.add_parser("draft")
    draft.add_argument(
        "--recommendation",
        default="strong_apply,apply,review",
    )
    draft.add_argument("--retry-failed", action="store_true")
    draft.add_argument("--limit", type=int)
    application_report = commands.add_parser("application-report")
    application_report.add_argument(
        "--format", choices=("table", "json"), default="table"
    )
    application_report.add_argument("--output")
    application_report.add_argument("--all-active", action="store_true")
    commands.add_parser("funnel-report", help="Show current pipeline funnel counts")
    telegram_preview = commands.add_parser("telegram-preview")
    telegram_preview.add_argument("--limit", type=int, default=3)
    telegram_send = commands.add_parser("telegram-send")
    telegram_send_scope = telegram_send.add_mutually_exclusive_group()
    telegram_send_scope.add_argument("--limit", type=int, default=3)
    telegram_send_scope.add_argument("--all", action="store_true")
    telegram_send.add_argument("--force", action="store_true")
    commands.add_parser("telegram-poll", help="Poll Telegram callback actions")
    commands.add_parser("screen")
    commands.add_parser("stats")
    run = commands.add_parser(
        "run",
        help="Run ingestion through Telegram for new or incomplete vacancies",
    )
    run.add_argument("--days", type=int, default=2)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--no-telegram", action="store_true")
    return root


def main() -> None:
    args = parser().parse_args()
    settings = Settings.from_env()

    if args.command == "search-add":
        _search_add(args)
    elif args.command in {"telegram-connect", "telegram-check"}:
        _telegram_connect(args, settings)
    elif args.command == "daily":
        _daily(args, settings)
    elif args.command == "application-set":
        _application_set(args)
    elif args.command == "scrape":
        _scrape(args)
    elif args.command == "backfill":
        _backfill(args, settings)
    elif args.command == "export":
        _export(args, settings)
    elif args.command == "enrich":
        _enrich(args, settings)
    elif args.command == "enrichment-report":
        _enrichment_report(args, settings)
    elif args.command == "score":
        _score(args, settings)
    elif args.command == "draft":
        _draft(args, settings)
    elif args.command == "application-report":
        _application_report(args, settings)
    elif args.command == "funnel-report":
        _funnel_report(settings)
    elif args.command == "telegram-preview":
        _telegram_preview(args, settings)
    elif args.command == "telegram-send":
        _telegram_send(args, settings)
    elif args.command == "telegram-poll":
        _telegram_poll(settings)
    elif args.command == "screen":
        _screen(settings)
    elif args.command == "stats":
        _stats(settings)
    elif args.command == "run":
        _run(args, settings)


if __name__ == "__main__":
    main()
