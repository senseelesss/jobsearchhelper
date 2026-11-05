from collections.abc import Iterable

from job_radar.models import EmailMessage, Job
from job_radar.parsers import parse_email


def extract_jobs(messages: Iterable[EmailMessage]) -> list[Job]:
    unique: dict[str, Job] = {}

    for message in messages:
        for job in parse_email(message):
            unique.setdefault(job.dedupe_key, job)

    return list(unique.values())

