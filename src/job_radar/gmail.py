import base64
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Iterator

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google_auth_httplib2 import AuthorizedHttp
import httplib2

from job_radar.models import EmailMessage

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class BoundedRefreshRequest:
    def __call__(self, *args, **kwargs):
        kwargs["timeout"] = 20
        return Request()(*args, **kwargs)


def _credentials(credentials_path: Path, token_path: Path, *, interactive=True) -> Credentials:
    credentials: Credentials | None = None

    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(token_path, SCOPES)

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(BoundedRefreshRequest())
    elif not credentials or not credentials.valid:
        if not interactive:
            raise RuntimeError("Gmail needs interactive reauthorization before unattended use")
        if not credentials_path.exists():
            raise FileNotFoundError(
                f"Missing OAuth client file: {credentials_path}. See README.md."
            )
        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
        credentials = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    token_path.chmod(0o600)
    return credentials


def gmail_service(credentials_path: Path, token_path: Path, *, interactive=True) -> Any:
    credentials = _credentials(credentials_path, token_path, interactive=interactive)
    return build(
        "gmail",
        "v1",
        http=AuthorizedHttp(credentials, http=httplib2.Http(timeout=20)),
        cache_discovery=False,
    )


def list_message_ids(service: Any, query: str, limit: int | None = None) -> Iterator[str]:
    page_token: str | None = None
    emitted = 0

    while True:
        request_limit = min(500, limit - emitted) if limit else 500
        response = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=request_limit, pageToken=page_token)
            .execute()
        )

        for message in response.get("messages", []):
            yield message["id"]
            emitted += 1
            if limit and emitted >= limit:
                return

        page_token = response.get("nextPageToken")
        if not page_token:
            return


def _body(message: Message, content_type: str) -> str:
    if message.get_content_type() == content_type:
        return message.get_content()

    parts: list[str] = []
    for part in message.walk():
        if part.get_content_type() == content_type and not part.get_filename():
            parts.append(part.get_content())
    return "\n".join(parts)


def decode_raw_message(payload: dict[str, Any]) -> EmailMessage:
    raw = payload["raw"]
    padding = "=" * (-len(raw) % 4)
    message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw + padding))

    return EmailMessage(
        message_id=payload["id"],
        received_at=message.get("Date", ""),
        sender=message.get("From", ""),
        subject=message.get("Subject", ""),
        text=_body(message, "text/plain"),
        html=_body(message, "text/html"),
    )


def fetch_message(service: Any, message_id: str) -> EmailMessage:
    payload = service.users().messages().get(userId="me", id=message_id, format="raw").execute()
    return decode_raw_message(payload)


def fetch_messages(service: Any, query: str, limit: int | None = None) -> Iterator[EmailMessage]:
    for message_id in list_message_ids(service, query, limit):
        yield fetch_message(service, message_id)
