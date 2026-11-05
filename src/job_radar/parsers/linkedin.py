import re

from job_radar.models import EmailMessage, Job
from job_radar.parsers.common import attribute, clean_text

JOB_ID_PATTERN = re.compile(r"linkedin\.com/comm/jobs/view/(\d+)", re.IGNORECASE)
CARD_START_PATTERN = re.compile(
    r"<[^>]+\bdata-test-id\s*=\s*([\"'])job-card\1[^>]*>", re.IGNORECASE
)
ANCHOR_PATTERN = re.compile(r"<a\b([^>]*)>([\s\S]*?)</a>", re.IGNORECASE)


def _cards(document: str) -> list[str]:
    starts = list(CARD_START_PATTERN.finditer(document))
    return [
        document[match.start() : starts[index + 1].start() if index + 1 < len(starts) else None]
        for index, match in enumerate(starts)
    ]


def _find_title(card: str, job_id: str) -> str:
    candidates: list[str] = []
    for anchor in ANCHOR_PATTERN.finditer(card):
        href = attribute(anchor.group(1), "href")
        if re.search(rf"/jobs/view/{re.escape(job_id)}(?:/|\?)", href):
            value = clean_text(anchor.group(2))
            if value:
                candidates.append(value)
    return max(candidates, key=len, default="")


def parse_linkedin(message: EmailMessage) -> list[Job]:
    if "has been created" in message.subject.lower():
        return []

    jobs: list[Job] = []

    for card in _cards(message.html):
        match = JOB_ID_PATTERN.search(card)
        if not match:
            continue

        job_id = match.group(1)
        title = _find_title(card, job_id)
        if not title:
            continue

        meta_match = re.search(
            r"<p\b[^>]*class\s*=\s*([\"'])[^\"']*text-xs[^\"']*\1[^>]*>"
            r"([\s\S]*?)</p>",
            card,
            re.IGNORECASE,
        )
        meta = clean_text(meta_match.group(2)) if meta_match else ""
        meta_parts = re.split(r"\s*[·•]\s*", meta)

        jobs.append(
            Job(
                source="linkedin",
                source_job_id=job_id,
                title=title,
                company=meta_parts[0] if meta_parts else "",
                location=" · ".join(meta_parts[1:]),
                snippet="",
                url=f"https://www.linkedin.com/jobs/view/{job_id}/",
                email_message_id=message.message_id,
                email_received_at=message.received_at,
                alert_subject=message.subject,
            )
        )

    return jobs
