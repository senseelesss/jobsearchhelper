import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    credentials_path: Path
    token_path: Path
    enrichment_timeout_seconds: float = 15.0
    enrichment_rate_limit_seconds: float = 1.0
    enrichment_retries: int = 2
    enrichment_freshness_hours: float = 72
    enrichment_retry_hours: float = 24
    candidate_profile_path: Path = Path("data/candidate_profile.yaml")
    ai_run_budget_usd: float = 0
    ai_day_budget_usd: float = 0
    openai_api_key: str = ""
    openai_model: str = "gpt-5.4-nano"
    openai_input_cost_per_million: float = 0.20
    openai_output_cost_per_million: float = 1.25
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    openai_timeout_seconds: float = 30
    openai_max_retries: int = 0
    openai_max_output_tokens: int = 1200
    openai_max_input_bytes: int = 48000

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(".env", override=False)
        load_dotenv("data/.env", override=False)
        return cls(
            database_path=Path(os.getenv("JOB_RADAR_DB", "data/jobs.db")),
            credentials_path=Path(
                os.getenv("JOB_RADAR_CREDENTIALS", "secrets/credentials.json")
            ),
            token_path=Path(os.getenv("JOB_RADAR_TOKEN", "secrets/token.json")),
            enrichment_timeout_seconds=float(
                os.getenv("JOB_RADAR_ENRICH_TIMEOUT", "15")
            ),
            enrichment_rate_limit_seconds=float(
                os.getenv("JOB_RADAR_ENRICH_RATE_LIMIT", "1")
            ),
            enrichment_retries=int(os.getenv("JOB_RADAR_ENRICH_RETRIES", "2")),
            enrichment_freshness_hours=float(os.getenv("JOB_RADAR_ENRICH_FRESHNESS_HOURS", "72")),
            enrichment_retry_hours=float(os.getenv("JOB_RADAR_ENRICH_RETRY_HOURS", "24")),
            candidate_profile_path=Path(
                os.getenv(
                    "JOB_RADAR_CANDIDATE_PROFILE",
                    "data/candidate_profile.yaml",
                )
            ),
            ai_run_budget_usd=float(os.getenv("JOB_RADAR_AI_RUN_BUDGET_USD", "0")),
            ai_day_budget_usd=float(os.getenv("JOB_RADAR_AI_DAY_BUDGET_USD", "0")),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.4-nano"),
            openai_input_cost_per_million=float(
                os.getenv("OPENAI_INPUT_COST_PER_MILLION", "0.20")
            ),
            openai_output_cost_per_million=float(
                os.getenv("OPENAI_OUTPUT_COST_PER_MILLION", "1.25")
            ),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            openai_timeout_seconds=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30")),
            openai_max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "0")),
            openai_max_output_tokens=int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "1200")),
            openai_max_input_bytes=int(os.getenv("OPENAI_MAX_INPUT_BYTES", "48000")),
        )
