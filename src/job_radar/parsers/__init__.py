from job_radar.models import EmailMessage, Job
from job_radar.parsers.indeed import parse_indeed
from job_radar.parsers.linkedin import parse_linkedin


def parse_email(message: EmailMessage) -> list[Job]:
    sender = message.sender.lower()

    if "jobalerts-noreply@linkedin.com" in sender:
        return parse_linkedin(message)
    if "donotreply@jobalert.indeed.com" in sender:
        return parse_indeed(message)
    return []


__all__ = ["parse_email"]

