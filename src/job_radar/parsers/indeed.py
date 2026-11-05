import html
import hashlib
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit

from job_radar.models import EmailMessage, Job
from job_radar.parsers.common import clean_text

JOB_URL_PATTERN = re.compile(
    r"https?://at\.indeed\.com/"
    r"(?:(?:rc|pagead)/clk/dl|(?:m/)?viewjob)(?:\?[^\s<>\"']*)?",
    re.IGNORECASE,
)
NOISE_PATTERN = re.compile(
    r"^(Einfach bewerben|Neu|Dringend gesucht|Heute|Gerade geschaltet|Vor \d+"
    r"|Indeed Job Alert|Jobs \d|See matching results)",
    re.IGNORECASE,
)
HEADER_PATTERN = re.compile(
    r"^\d+\s+.+\bJobs?\s+(?:in|near|für)\b",
    re.IGNORECASE,
)


def _job_id(url: str, title: str, company: str, location: str) -> str:
    query = parse_qs(urlsplit(html.unescape(url)).query)
    job_ids = query.get("jk", [])
    if job_ids and re.fullmatch(r"[a-z0-9]+", job_ids[0], re.IGNORECASE):
        return job_ids[0]

    identity = "\n".join(
        clean_text(value).casefold() for value in (title, company, location)
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return f"sponsored-{digest}"


def _job_url(url: str, job_id: str) -> str:
    if not job_id.startswith("sponsored-"):
        return f"https://at.indeed.com/viewjob?jk={job_id}"

    parts = urlsplit(html.unescape(url))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def parse_indeed(message: EmailMessage) -> list[Job]:
    if re.search(r"bestätigen|confirm", message.subject, re.IGNORECASE):
        return []

    source_text = message.text
    if not source_text:
        html_with_urls = re.sub(
            r"<a\b[^>]*href\s*=\s*([\"'])(.*?)\1[^>]*>[\s\S]*?</a>",
            lambda match: html.unescape(match.group(2)),
            message.html,
            flags=re.IGNORECASE,
        )
        source_text = "\n".join(
            clean_text(fragment)
            for fragment in re.split(r"<br\s*/?>", html_with_urls, flags=re.IGNORECASE)
        )

    lines = [line.strip() for line in source_text.splitlines() if line.strip()]
    jobs: list[Job] = []
    block_start = 0

    for index, line in enumerate(lines):
        match = JOB_URL_PATTERN.search(line)
        if not match:
            continue

        block = lines[block_start:index]
        block_start = index + 1
        block = [value for value in block if not NOISE_PATTERN.match(value)]

        # Search-result links belong to the email header, not to a vacancy.
        block = [value for value in block if "at.indeed.com/jobs?" not in value]
        block = [value for value in block if not HEADER_PATTERN.match(value)]
        if len(block) < 2:
            continue

        title = block[0]
        company_location = block[1].split(" - ")
        company = company_location[0]
        location = " - ".join(company_location[1:])
        job_id = _job_id(match.group(0), title, company, location)
        snippet = " ".join(
            value for value in block[2:] if not NOISE_PATTERN.match(value)
        )

        jobs.append(
            Job(
                source="indeed",
                source_job_id=job_id,
                title=title,
                company=company,
                location=location,
                snippet=snippet[:700],
                url=_job_url(match.group(0), job_id),
                email_message_id=message.message_id,
                email_received_at=message.received_at,
                alert_subject=message.subject,
            )
        )

    return jobs
