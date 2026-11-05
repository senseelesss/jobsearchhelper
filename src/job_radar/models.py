import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass


def normalize_semantic_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def semantic_key(title: str, company: str, location: str) -> str:
    identity = "\n".join(
        normalize_semantic_text(value) for value in (title, company, location)
    )
    return f"semantic-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"


@dataclass(frozen=True, slots=True)
class EmailMessage:
    message_id: str
    received_at: str
    sender: str
    subject: str
    text: str
    html: str


@dataclass(frozen=True, slots=True)
class Job:
    source: str
    source_job_id: str
    title: str
    company: str
    location: str
    snippet: str
    url: str
    email_message_id: str = ""
    email_received_at: str = ""
    alert_subject: str = ""
    observed_at: str = ""
    connector: str = ""
    search_url: str = ""
    published_at: str = ""

    @property
    def dedupe_key(self) -> str:
        return f"{self.source}:{self.source_job_id}"

    @property
    def semantic_key(self) -> str:
        return semantic_key(self.title, self.company, self.location)

    def to_dict(self) -> dict[str, str]:
        return {
            "dedupe_key": self.dedupe_key,
            "semantic_key": self.semantic_key,
            **asdict(self),
        }
