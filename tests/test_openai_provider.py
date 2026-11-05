from types import SimpleNamespace

from job_radar.ai.openai_provider import OpenAIProvider
from job_radar.application_models import ApplicationDraftContent, JobScore


def score() -> JobScore:
    return JobScore(
        match_score=82,
        score_confidence="high",
        provisional=False,
        recommendation="apply",
        matching_strengths=["Python"],
        missing_requirements=[],
        risk_flags=[],
        role_summary="Backend role.",
    )


class FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        text_format = kwargs["text_format"]
        if text_format is JobScore:
            output = score()
        else:
            output = ApplicationDraftContent(
                cover_letter_language="en",
                cover_letter="Application text",
                recruiter_message="Recruiter text",
            )
        return SimpleNamespace(
            id="resp_123",
            output_parsed=output,
            usage=SimpleNamespace(
                input_tokens=321,
                output_tokens=123,
                total_tokens=444,
            ),
        )


def vacancy() -> dict[str, object]:
    return {
        "title": "Junior Backend Engineer",
        "company": "Example GmbH",
        "location": "Vienna, Austria",
        "description": "Python backend role.",
        "description_language": "en",
        "description_confidence": "high",
        "fetch_status": "success",
        "screening_status": "eligible",
    }


def test_openai_provider_separates_score_and_draft_responses() -> None:
    responses = FakeResponses()
    client = SimpleNamespace(responses=responses)
    provider = OpenAIProvider(
        api_key="test-key",
        model="test-model",
        client=client,
    )

    score_result = provider.score({"name": "Candidate"}, vacancy(), False)
    draft_result = provider.draft(
        {"name": "Candidate"}, vacancy(), score_result.score
    )
    rewrite_result = provider.rewrite_draft(
        {"name": "Candidate"},
        vacancy(),
        score_result.score,
        draft_result.draft,
    )

    assert responses.calls[0]["text_format"] is JobScore
    assert responses.calls[1]["text_format"] is ApplicationDraftContent
    assert responses.calls[2]["text_format"] is ApplicationDraftContent
    assert score_result.score.match_score == 82
    assert draft_result.draft.cover_letter == "Application text"
    assert rewrite_result.usage.total_tokens == 444
    assert responses.calls[0]["store"] is False
    assert responses.calls[0]["max_output_tokens"] == 1200


def test_oversize_ai_input_is_rejected_before_request():
    import pytest
    responses = FakeResponses()
    provider = OpenAIProvider("test-key", "test-model", client=SimpleNamespace(responses=responses),
                              max_input_bytes=1024)
    with pytest.raises(ValueError, match="byte limit"):
        provider.score({"facts": "x" * 2000}, vacancy(), False)
    assert responses.calls == []
