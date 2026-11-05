from job_radar.ai.provider import (
    AIProvider,
    DraftProviderResult,
    ScoreProviderResult,
    TokenUsage,
)

__all__ = [
    "AIProvider",
    "DraftProviderResult",
    "OpenAIProvider",
    "ScoreProviderResult",
    "TokenUsage",
]


def __getattr__(name):
    if name == "OpenAIProvider":
        from job_radar.ai.openai_provider import OpenAIProvider

        globals()[name] = OpenAIProvider
        return OpenAIProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
