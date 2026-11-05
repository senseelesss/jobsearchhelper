from pathlib import Path

from job_radar.models import EmailMessage
from job_radar.parsers.indeed import parse_indeed
from job_radar.parsers.linkedin import parse_linkedin

FIXTURES = Path(__file__).parent / "fixtures"


def message(sender: str, html: str, subject: str = "Jobs") -> EmailMessage:
    return EmailMessage(
        message_id="email-1",
        received_at="2026-06-23T08:00:00Z",
        sender=sender,
        subject=subject,
        text="",
        html=html,
    )


def test_linkedin_parser_extracts_clean_job() -> None:
    html = (FIXTURES / "linkedin.html").read_text()
    jobs = parse_linkedin(message("LinkedIn <jobalerts-noreply@linkedin.com>", html))

    assert len(jobs) == 1
    assert jobs[0].source_job_id == "4428938286"
    assert jobs[0].title == "IT & Innovation Internship (Part-Time, Hybrid/Remote)"
    assert jobs[0].company == "EquiForensics"
    assert jobs[0].location == "Vienna, Austria (Hybrid)"
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/4428938286/"


def test_indeed_parser_extracts_clean_job() -> None:
    html = (FIXTURES / "indeed.html").read_text()
    jobs = parse_indeed(message("Indeed <donotreply@jobalert.indeed.com>", html))

    assert len(jobs) == 1
    assert jobs[0].source_job_id == "ca7d702559ef1e24"
    assert jobs[0].title == "Student Job (f/m/x) - Agile Delivery"
    assert jobs[0].company == "Raiffeisen Bank International AG"
    assert jobs[0].location == "Wien, W"
    assert "Curiosity for AI" in jobs[0].snippet
    assert jobs[0].url == "https://at.indeed.com/viewjob?jk=ca7d702559ef1e24"


def test_indeed_parser_ignores_alert_header_as_title() -> None:
    html = (FIXTURES / "indeed_header.html").read_text()
    jobs = parse_indeed(message("Indeed <donotreply@jobalert.indeed.com>", html))

    assert len(jobs) == 1
    assert jobs[0].source_job_id == "headerjob123"
    assert jobs[0].title == "Backend Automation Intern"
    assert jobs[0].company == "Example Systems GmbH"


def test_indeed_sponsored_url_terminates_block_without_shifting_jobs() -> None:
    html = (FIXTURES / "indeed_sponsored_pair.html").read_text()
    jobs = parse_indeed(message("Indeed <donotreply@jobalert.indeed.com>", html))

    assert len(jobs) == 3
    assert jobs[0].source_job_id.startswith("sponsored-")
    assert jobs[1].source_job_id.startswith("sponsored-")
    assert jobs[0].source_job_id != jobs[1].source_job_id
    assert jobs[0].url == (
        "https://at.indeed.com/pagead/clk/dl?ad=alpha123&from=ja&tracking=one"
    )
    assert jobs[1].url == (
        "https://at.indeed.com/pagead/clk/dl?ad=beta456&from=ja&tracking=two"
    )
    assert jobs[2].source_job_id == "bitpanda456"
    assert jobs[2].title == "Junior Backend Engineer"
