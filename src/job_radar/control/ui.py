"""Owner-only routing and plain-text Telegram views; no collection business logic."""

import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit

from job_radar.control.catalog import label, tr
from job_radar.control.jobs import FILTERS, SORTS, VIEWS
from job_radar.telegram import TelegramRejectedRequest, process_callback

logger = logging.getLogger(__name__)


def clip(text, units=3900):
    """Telegram counts UTF-16 units, not Python code points."""
    raw = str(text).encode("utf-16-le", errors="replace")
    if len(raw) <= units * 2:
        return str(text)
    return raw[: (units - 1) * 2].decode("utf-16-le", errors="ignore") + "…"


def public_url(value):
    try:
        parts = urlsplit(str(value))
        if (
            parts.scheme in {"http", "https"}
            and parts.hostname
            and not parts.username
            and not parts.password
        ):
            return str(value)
    except ValueError:
        pass
    return None


class ControlCenter:
    def __init__(self, jobs, search, transport, owner_id: int, chat_id: str):
        if owner_id <= 0 or str(owner_id) != str(chat_id):
            raise ValueError("Control Center requires the owner's numeric private chat ID")
        self.jobs, self.search, self.transport = jobs, search, transport
        self.store, self.repository = jobs.store, jobs.repository
        self.owner_id, self.chat_id = owner_id, str(chat_id)
        self.message_id = None
        self.language = "uk"

    def t(self, key, **values):
        return tr(self.language, key, **values)

    def b(self, ui_key, action, **values):
        return {
            "text": self.t(ui_key),
            "callback_data": self.store.token({"action": action, **values}),
        }

    def named(self, text, action, **values):
        return {
            "text": clip(text, 60),
            "callback_data": self.store.token({"action": action, **values}),
        }

    def show(self, text, rows=()):
        markup = {"inline_keyboard": list(rows)}
        if self.message_id is None:
            self.message_id = self.transport.send_message(self.chat_id, clip(text), markup)
            return
        try:
            self.transport.edit_message(self.chat_id, self.message_id, clip(text), markup)
        except TelegramRejectedRequest as error:
            if getattr(error, "not_modified", False):
                return
            # A definite rejection permits a replacement. Ambiguous network errors do not.
            self.message_id = self.transport.send_message(self.chat_id, clip(text), markup)

    def nav(self, action="home", **values):
        return [self.b("back", action, **values), self.b("menu", "home")]

    def handle(self, update):
        callback = update.get("callback_query")
        message = (callback or {}).get("message") if callback else update.get("message")
        message = message or {}
        sender = (callback or message).get("from") or {}
        chat = message.get("chat") or {}
        if (
            sender.get("id") != self.owner_id
            or str(chat.get("id")) != self.chat_id
            or chat.get("type", "private") != "private"
        ):
            if callback:
                self.transport.answer_callback(str(callback.get("id", "")), tr("uk", "denied"))
            return
        self.language = self.store.get("language", "uk")
        self.message_id = int(message["message_id"]) if callback else None
        try:
            self.store.prune()
            if callback:
                data = str(callback.get("data", ""))
                if not data.startswith("cc:"):
                    if data.startswith(("applyjob:", "skipjob:")):
                        self.transport.answer_callback(str(callback.get("id", "")))
                        key = data.split(":", 1)[1]
                        row = self.jobs.detail(key) if len(data.encode()) <= 64 else None
                        if (
                            row is None
                            or not self.jobs.legacy_identity_matches(row, self.chat_id)
                            or row["application_state"] not in {"not_applied", "saved"}
                        ):
                            self.show(self.t("expired"), [self.nav()])
                            return
                        state = "applied" if data.startswith("applyjob:") else "skipped"
                        self.repository.set_application(
                            row["application_key"],
                            state,
                            "telegram:" + str(callback.get("id", "")),
                            actor="telegram",
                            expected_revision=row["application_revision"],
                        )
                        self.jobs.last_sync = 0
                        self.detail(key)
                        return
                    # Legacy digest/cover-letter actions retain their established ledger behavior.
                    process_callback(
                        self.repository,
                        self.transport,
                        self.chat_id,
                        callback,
                        model=self.jobs.model,
                        language=self.language,
                    )
                    return
                self.transport.answer_callback(str(callback.get("id", "")))
                payload = self.store.resolve(data)
                if payload is None:
                    self.show(self.t("expired"), [self.nav()])
                    return
                self.route(payload, data)
            else:
                parts = str(message.get("text", "")).split(maxsplit=1)
                command = parts[0].split("@")[0] if parts else ""
                if command == "/help":
                    self.show(self.t("help"), [self.nav()])
                elif command in {"/start", "/menu", "/cancel"}:
                    self.home()
        except Exception as error:
            # Never log job evidence, callback bodies, transport URLs, or exception text.
            logger.warning("telegram_action_failed", extra={"error_type": type(error).__name__})
            try:
                self.show(self.t("error"), [self.nav()])
            except Exception:
                logger.warning("telegram_error_reply_failed")

    def route(self, p, token=""):
        action = p["action"]
        if action == "home":
            self.home()
        elif action == "browse":
            self.browse(
                p.get("view", self.store.get("default_view", "recommendations")),
                p.get("sort", "newest"),
                p.get("filters", {}),
                p.get("since"),
            )
        elif action == "page":
            snapshot = p.get("snapshot") or token[3:]
            self.page(snapshot, p.get("page", 0))
        elif action == "detail":
            self.detail(p["key"], p.get("snapshot"), p.get("position"))
        elif action == "status":
            self.change_status(p, token)
        elif action == "settings":
            self.settings()
        elif action == "language":
            if p["value"] not in {"uk", "de", "en"}:
                raise ValueError("Invalid language")
            self.language = p["value"]
            self.store.set("language", self.language)
            self.settings()
        elif action == "notifications":
            # Button encodes the desired value: replay never toggles it back.
            self.store.set("notifications", bool(p["value"]))
            self.settings()
        elif action == "default_view":
            if p["value"] not in VIEWS:
                raise ValueError("Invalid view")
            self.store.set("default_view", p["value"])
            self.settings()
        elif action == "search":
            self.search_menu()
        elif action == "select":
            valid = {
                "all",
                *["p:" + x.name for x in self.search.profiles()],
                *["s:" + x.source for x in self.search.profiles()],
            }
            if p["value"] not in valid:
                raise ValueError("Invalid selection")
            self.store.set("search_selection", p["value"])
            self.search_menu()
        elif action == "depth":
            if not 1 <= p["value"] <= self.search.config.max_pages:
                raise ValueError("Invalid depth")
            self.store.set("search_pages", p["value"])
            self.search_menu()
        elif action == "start_search":
            # A replay of this token can only display its original request outcome.
            existing = self.store.get("request:" + token)
            if not existing:
                existing = self.search.start(
                    self.store.get("search_selection", "all"), self.store.get("search_pages", 1)
                )
                self.store.set("request:" + token, existing)
            self.run_status(
                existing if existing["status"] in {"cooldown", "config_missing"} else None
            )
        elif action == "run":
            self.run_status()
        elif action == "statistics":
            self.statistics()
        elif action == "filters":
            self.filters(p)
        elif action == "filter_values":
            self.filter_values(p)
        elif action == "filter_set":
            filters = {**p.get("filters", {}), p["field"]: p["value"]}
            self.browse(p["view"], p.get("sort", "newest"), filters, p.get("since"))
        elif action in {"ai", "draft"}:
            row = self.jobs.detail(p["key"])
            if row is None:
                self.show(self.t("unavailable"), [self.nav()])
                return
            text = self.t("ai_gap")
            if action == "draft":
                draft = self.repository.latest_application_draft(
                    row.get("score_cache_key") or row.get("historical_cache_key", "")
                )
                result = (
                    json.loads(draft["result_json"])
                    if draft and draft["draft_status"] == "ready"
                    else {}
                )
                text = result.get("cover_letter") or self.t("no_draft")
            self.show(
                text,
                [
                    self.nav(
                        "detail",
                        key=p["key"],
                        snapshot=p.get("snapshot"),
                        position=p.get("position"),
                    )
                ],
            )
        else:
            self.show(self.t("expired"), [self.nav()])

    def home(self):
        stats = self.jobs.statistics()
        lines = [self.t("home")]
        for key in ("new", "unreviewed", "saved", "applications"):
            value = stats["total"] if key == "new" else stats[key]
            if key == "new":
                value = sum(n for state, n in stats["state"] if state == "not_applied")
            lines.append(f"{self.t(key)}: {value}")
        success = self.repository.connection.execute(
            "SELECT ended_at FROM run_history WHERE status='success' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last = self.repository.connection.execute(
            "SELECT status FROM run_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        lines += [
            f"{self.t('last_success')}: {success[0] if success else self.t('unavailable')}",
            f"{self.t('last_run')}: {label(self.language, last[0]) if last else self.t('idle')}",
        ]
        self.show(
            "\n".join(lines),
            [
                [
                    self.b("find", "search"),
                    self.b("recommendations", "browse", view="recommendations"),
                ],
                [
                    self.b("saved", "browse", view="saved"),
                    self.b("applications", "browse", view="applications"),
                ],
                [self.b("statistics", "statistics"), self.b("settings", "settings")],
                [self.b("all", "browse", view="all"), self.b("default_view", "browse")],
            ],
        )

    def browse(self, view, sort="newest", filters=None, since=None):
        snapshot = self.jobs.snapshot(view, sort, filters, since)
        self.page(snapshot)

    def page(self, snapshot, page=0):
        original = self.store.resolve("cc:" + snapshot)
        if original is None:
            self.show(self.t("expired"), [self.nav()])
            return
        rows, more = self.jobs.page(snapshot, page)
        buttons, lines = [], [self.t(original["view"]), self.t("page", page=page + 1)]
        for i, item in enumerate(rows):
            row = self.jobs.detail(item["key"])
            if row is None:
                continue
            title = clip(row["title"], 65)
            lines.append(
                f"\n{page * 5 + i + 1}. {title}\n{clip(row['company'], 50)} · {row['source']} · {label(self.language, row['application_priority'])}"
            )
            buttons.append(
                [
                    self.named(
                        title,
                        "detail",
                        key=row["dedupe_key"],
                        snapshot=snapshot,
                        position=page * 5 + i + 1,
                    )
                ]
            )
        if not rows:
            lines.append(self.t("empty"))
        navigation = []
        if page:
            navigation.append(self.b("previous", "page", snapshot=snapshot, page=page - 1))
        if more:
            navigation.append(self.b("next", "page", snapshot=snapshot, page=page + 1))
        if navigation:
            buttons.append(navigation)
        options = {key: original.get(key) for key in ("view", "sort", "filters", "since")}
        buttons.append([self.b("filters", "filters", **options)])
        buttons.append([self.b(key, "browse", **{**options, "sort": key}) for key in sorted(SORTS)])
        buttons.extend(
            [
                [self.b(key, "browse", view=key) for key in group]
                for group in (("new", "recommendations", "saved"), ("applied", "skipped", "all"))
            ]
        )
        buttons.append(self.nav())
        self.show("\n".join(lines), buttons)

    def detail(self, key, snapshot=None, position=None):
        row = self.jobs.detail(key)
        if row is None:
            self.show(self.t("unavailable"), [self.nav()])
            return
        self.store.set("reviewed:" + row["semantic_key"], True)
        self.jobs._statistics = None
        text = [clip(row["title"], 250), clip(row["company"], 160), "", self.t("confirmed")]
        for key_, value in (
            ("location", row["location"]),
            ("source", row["source"]),
            ("discovered", row["created_at"]),
        ):
            text.append(f"{self.t(key_)}: {clip(value or self.t('unknown'), 240)}")
        text += [
            "",
            self.t("inferred"),
            f"{self.t('priority')}: {label(self.language, row['application_priority'])}",
            f"{self.t('work_mode')}: {label(self.language, row['work_mode'])}",
            f"{self.t('hours')}: {label(self.language, row['hours_fit'])}",
            f"{self.t('jd')}: {self.t('full' if row['full_jd'] else 'partial_jd')}",
            f"{self.t('status')}: {label(self.language, row['application_state'])}",
        ]
        if row.get("application_date"):
            text.append(f"{self.t('application_date')}: {row['application_date']}")
        if row.get("applied_at"):
            text.append(f"{self.t('applied')}: {row['applied_at']}")
        if row.get("score_cache_key") or row.get("historical_cache_key"):
            text.append(self.t("cached"))
        score_state = (
            row["score_status"] + "_score"
            if row["score_status"] in {"success", "failed"}
            else row["score_status"]
        )
        text.append(f"{self.t('score_state')}: {label(self.language, score_state)}")
        if row.get("match_score") is not None:
            text += [
                f"{self.t('ai')}: {row['match_score']}/100",
                f"{self.t('cost')}: {row['score_cost']}",
            ]
        elif row.get("historical_score") is not None:
            text += [
                f"{self.t('stored_score')}: {row['historical_score']}/100",
                f"{self.t('cost')}: {row['historical_cost']}",
            ]
        evidence = row.get("hours_evidence")
        if evidence and evidence != "not stated":
            text.append(clip(evidence, 300))
        text += ["", self.t("why") + ":"]
        for reason in row.get("eligibility_reasons", [])[:8]:
            text.append("• " + label(self.language, reason["code"]))
        for evidence in (row.get("matching_strengths") or [])[:2] + (
            row.get("missing_requirements") or []
        )[:2]:
            text.append("• " + clip(evidence, 180))
        text += [
            "",
            self.t("description") + ":",
            clip(row.get("description") or row.get("snippet") or self.t("unknown"), 1100),
        ]
        context = {"key": row["dedupe_key"], "snapshot": snapshot, "position": position}
        state_context = {
            **context,
            "target": row["application_key"],
            "semantic": row["semantic_key"],
            "revision": row["application_revision"],
        }
        buttons = []
        url = public_url(row["url"])
        if url:
            buttons.append([{"text": self.t("open"), "url": url}])
        buttons.append(
            [
                self.b(text_, "status", state=state, **state_context)
                for text_, state in (("save", "saved"), ("apply", "applied"), ("skip", "skipped"))
            ]
        )
        for states in (("interview", "offer", "rejected"), ("withdrawn", "not_applied")):
            buttons.append(
                [self.b(state, "status", state=state, **state_context) for state in states]
            )
        buttons.append([self.b("ai_review", "ai", **context), self.b("draft", "draft", **context)])
        navigation = []
        if snapshot and position:
            for delta, name in ((-1, "previous"), (1, "next")):
                other = self.jobs.position(snapshot, position + delta)
                if other:
                    navigation.append(
                        self.b(
                            name, "detail", key=other, snapshot=snapshot, position=position + delta
                        )
                    )
            buttons.append(navigation)
            buttons.append(self.nav("page", snapshot=snapshot, page=(position - 1) // 5))
        else:
            buttons.append(self.nav())
        self.show("\n".join(text), [r for r in buttons if r])

    def change_status(self, p, token):
        row = self.jobs.detail(p["key"])
        target = self.jobs.detail(p["target"])
        if (
            not row
            or not target
            or row["semantic_key"] != p["semantic"]
            or target["semantic_key"] != p["semantic"]
        ):
            raise ValueError("Vacancy identity changed")
        try:
            self.repository.set_application(
                p["target"], p["state"], token, actor="telegram", expected_revision=p["revision"]
            )
        except ValueError:
            self.show(self.t("expired"), [self.nav("detail", key=p["key"])])
            return
        self.jobs.last_sync = 0
        self.detail(p["key"], p.get("snapshot"), p.get("position"))

    def filters(self, p):
        options = {key: p.get(key) for key in ("view", "sort", "filters", "since")}
        buttons = [
            [
                self.b(
                    "status" if field == "state" else field, "filter_values", field=field, **options
                )
            ]
            for field in sorted(FILTERS)
        ]
        buttons += [
            [self.b("clear", "browse", view=p["view"], sort=p.get("sort", "newest"))],
            self.nav(),
        ]
        active = "\n".join(
            f"{self.t('status' if k == 'state' else k)}: {label(self.language, v)}"
            for k, v in (p.get("filters") or {}).items()
        )
        self.show(self.t("filters") + "\n" + active, buttons)

    def filter_values(self, p):
        options = {key: p.get(key) for key in ("view", "sort", "filters", "since", "field")}
        buttons = [
            [self.named(label(self.language, value), "filter_set", value=value, **options)]
            for value in self.jobs.options(p["field"])
        ]
        self.show(self.t("filters"), buttons + [self.nav()])

    def settings(self):
        notifications = self.store.get("notifications", True)
        text = [
            self.t("settings"),
            f"{self.t('language')}: {self.language}",
            f"{self.t('notifications')}: {self.t('on' if notifications else 'off')}",
            self.t("notification_note"),
            self.t("budget"),
        ]
        rows = [
            [
                self.named(name, "language", value=code)
                for code, name in (("uk", "Українська"), ("de", "Deutsch"), ("en", "English"))
            ],
            [self.b("off" if notifications else "on", "notifications", value=not notifications)],
        ]
        rows.append(
            [self.b(view, "default_view", value=view) for view in ("new", "recommendations", "all")]
        )
        rows += [[self.b("profiles", "search")], self.nav()]
        self.show("\n".join(text), rows)

    def search_menu(self):
        profiles = self.search.profiles()
        config = self.search.config
        text = self.t(
            "search_config", count=len(profiles), limit=config.limit, seconds=config.timeout
        )
        selection = self.store.get("search_selection", "all")
        text += f"\n{self.t('profiles')}: {self.t('all') if selection == 'all' else selection[2:]}"
        text += f"\n{self.t('depth')}: {self.store.get('search_pages', 1)}"
        rows = []
        if profiles:
            rows += [
                [self.b("start_search", "start_search")],
                [self.b("all", "select", value="all")],
            ]
            rows += [[self.named(p.name, "select", value="p:" + p.name)] for p in profiles]
            rows += [
                [self.named(source, "select", value="s:" + source)]
                for source in sorted({p.source for p in profiles})
            ]
            rows += [
                [
                    self.named(str(page), "depth", value=page)
                    for page in range(1, config.max_pages + 1)
                ]
            ]
        else:
            text += "\n" + self.t("config_missing")
        rows += [[self.b("refresh", "run")], self.nav()]
        self.show(text, rows)

    def run_status(self, state=None):
        state = state or self.search.status()
        status = state["status"]
        text = self.t(status, seconds=state.get("seconds", 0))
        if state.get("started"):
            end = state.get("ended", datetime.now(timezone.utc).timestamp())
            text += f"\n{self.t('duration')}: {round(end - state['started'])}"
        summary = state.get("summary") or {}
        for key, title in (("inserted", "inserted"), ("shortlisted", "relevant")):
            if key in summary:
                text += f"\n{self.t(title)}: {summary[key]}"
        if "sources" in summary:
            sources = summary["sources"]
            text += f"\n{self.t('source_result')}: {sum(s['status'] in {'success', 'empty'} for s in sources)}/{len(sources)}"
        rows = [[self.b("refresh", "run")]]
        if state.get("since"):
            rows.append([self.b("view_new", "browse", view="all", since=state["since"])])
        rows += [
            [self.b("recommendations", "browse", view="recommendations"), self.b("find", "search")],
            self.nav(),
        ]
        if status in {"running", "queued"}:
            stages = self.repository.connection.execute(
                "SELECT stage,status,item_count FROM run_stage_history WHERE run_id=("
                "SELECT id FROM run_history WHERE id>? ORDER BY id DESC LIMIT 1) LIMIT 12",
                (state.get("baseline", 0),),
            ).fetchall()
            for stage in stages:
                text += f"\n{clip(stage[0], 60)}: {label(self.language, stage[1])} ({stage[2]})"
            text += "\n" + self.t("run_note")
        self.show(text, rows)

    def statistics(self):
        stats = self.jobs.statistics()
        lines = [self.t("statistics")]
        for key in (
            "total",
            "today",
            "week",
            "full",
            "score_count",
            "saved",
            "applications",
            "skipped",
        ):
            lines.append(f"{self.t(key)}: {stats[key]}")
        for key in ("source", "priority", "state"):
            lines += ["", self.t("status" if key == "state" else key)]
            lines += [f"{label(self.language, name)}: {count}" for name, count in stats[key]]
        outbox = self.repository.connection.execute(
            "SELECT status,COUNT(*) FROM notification_outbox GROUP BY status"
        ).fetchall()
        lines += ["", self.t("notifications")]
        lines += [f"{label(self.language, r[0])}: {r[1]}" for r in outbox] or [self.t("empty")]
        lines += ["", self.t("run_history")]
        runs = self.repository.connection.execute(
            "SELECT started_at,status FROM run_history ORDER BY id DESC LIMIT 5"
        ).fetchall()
        lines += [f"{r[0]} · {label(self.language, r[1])}" for r in runs] or [self.t("empty")]
        self.show("\n".join(lines), [self.nav()])
