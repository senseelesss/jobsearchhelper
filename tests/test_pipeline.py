from job_radar.models import EmailMessage
from job_radar.pipeline import extract_jobs


def test_pipeline_ignores_unknown_sender() -> None:
    message = EmailMessage(
        message_id="x",
        received_at="",
        sender="newsletter@example.com",
        subject="News",
        text="",
        html="<p>Hello</p>",
    )

    assert extract_jobs([message]) == []

