"""Opt-in localization for existing immutable notification outboxes."""

import sqlite3
from pathlib import Path

from job_radar.control.catalog import label, tr
from job_radar.control.ui import clip, public_url


def preferences(repository):
    path = Path(repository.connection.execute("PRAGMA database_list").fetchone()[2])
    sidecar = path.with_suffix(path.suffix + ".telegram.sqlite3")
    if not sidecar.exists():
        return None  # Keep legacy templates until the owner enables the control center.
    import json

    with sqlite3.connect(sidecar.as_uri() + "?mode=ro", uri=True, timeout=5) as connection:
        values = dict(
            connection.execute(
                "SELECT key,value FROM preferences WHERE key IN ('language','notifications')"
            )
        )
    return {key: json.loads(value) for key, value in values.items()}


def digest(report, rows, day, language, store):
    def t(key):
        return tr(language, key)

    lines = [
        f"Job Radar · {day}",
        f"{t('inserted')}: {report['inserted']}",
        f"{t('relevant')}: {report['shortlisted']}",
        f"{t('cost')}: {report['estimated_cost_usd']:.6f}",
        t("partial" if report["errors"] else "success"),
    ]
    for source in report["sources"]:
        lines.append(
            f"{clip(source['name'], 70)}: {label(language, source['status'])} ({source['jobs']})"
        )
    text = clip("\n".join(lines), 850)
    buttons = []
    for row in rows:
        score = row.get("match_score")
        score_text = str(score) + "/100" if score is not None else label(language, "not_started")
        text += (
            f"\n\n{clip(row['title'], 75)} · {clip(row['company'], 35)}\n"
            f"{t('source')}: {clip(row['source'], 30)} · {score_text}\n"
            f"{label(language, row['application_priority'])} · {label(language, row['hours_fit'])}"
        )
        buttons.append(
            [
                {
                    "text": clip(row["title"], 45),
                    "callback_data": store.token({"action": "detail", "key": row["dedupe_key"]}),
                }
            ]
        )
    buttons.append(
        [
            {
                "text": t("recommendations"),
                "callback_data": store.token({"action": "browse", "view": "recommendations"}),
            },
            {"text": t("menu"), "callback_data": store.token({"action": "home"})},
        ]
    )
    return clip(text, 3800), buttons


def legacy_card(row, language, status="delivered"):
    def t(key):
        return tr(language, key)

    text = [
        str(row["title"]),
        str(row["company"]),
        str(row["location"]),
        f"{t('ai')}: {row['match_score']}/100",
        t("why") + ":",
    ]
    text += ["• " + clip(value, 180) for value in list(row["matching_strengths"])[:4]]
    text += ["• " + clip(value, 180) for value in list(row["missing_requirements"])[:3]]
    if status in {"applied", "skipped"}:
        text.append(t(status))
    return clip("\n".join(text))


def legacy_keyboard(row, language):
    key = row["semantic_key"]
    buttons = []
    url = public_url(row["url"])
    if url:
        buttons.append([{"text": tr(language, "open"), "url": url}])
    buttons += [
        [{"text": tr(language, "draft"), "callback_data": "cover:" + key}],
        [
            {"text": tr(language, "applied"), "callback_data": "applied:" + key},
            {"text": tr(language, "skip"), "callback_data": "skipped:" + key},
        ],
    ]
    return {"inline_keyboard": buttons}
