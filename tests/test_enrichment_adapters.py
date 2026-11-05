from pathlib import Path

import pytest

from job_radar.enrichment_adapters.indeed import IndeedAdapter
from job_radar.enrichment_adapters.linkedin import LinkedInAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def test_indeed_adapter_extracts_full_description() -> None:
    document = (FIXTURES / "indeed_vacancy.html").read_text()

    result = IndeedAdapter().parse(document, 200)

    assert result.status == "success"
    assert result.description_language == "en"
    assert "Build Python services" in result.description
    assert "automation projects" in result.description
    assert result.description_confidence == "medium"


def test_indeed_prefers_jobposting_json_ld() -> None:
    document = (FIXTURES / "indeed_jsonld_vacancy.html").read_text()

    result = IndeedAdapter().parse(document, 200)

    assert result.status == "success"
    assert result.description_language == "en"
    assert result.description_confidence == "high"
    assert "Build reliable Python services" in result.description
    assert "Fragile selector content" not in result.description


def test_linkedin_adapter_extracts_full_description() -> None:
    document = (FIXTURES / "linkedin_vacancy.html").read_text()

    result = LinkedInAdapter().parse(document, 200)

    assert result.status == "success"
    assert result.description_language == "de"
    assert "Automatisierungen" in result.description
    assert "Datenpipelines" in result.description
    assert result.description_confidence == "medium"


def test_language_detection_ignores_page_locale_for_english_description() -> None:
    document = (FIXTURES / "english_description_german_locale.html").read_text()

    result = LinkedInAdapter().parse(document, 200)

    assert result.description_language == "en"


def test_language_detection_ignores_page_locale_for_german_description() -> None:
    document = (FIXTURES / "german_description_english_locale.html").read_text()

    result = LinkedInAdapter().parse(document, 200)

    assert result.description_language == "de"


def test_language_detection_marks_mixed_text_unknown() -> None:
    document = (FIXTURES / "mixed_description.html").read_text()

    result = LinkedInAdapter().parse(document, 200)

    assert result.description_language == "unknown"


def test_indeed_detects_consent_login_page() -> None:
    document = (FIXTURES / "indeed_consent.html").read_text()

    result = IndeedAdapter().parse(document, 200)

    assert result.status == "blocked"


@pytest.mark.parametrize("adapter", [IndeedAdapter(), LinkedInAdapter()])
def test_adapters_detect_blocked_pages(adapter: object) -> None:
    document = (FIXTURES / "vacancy_blocked.html").read_text()

    result = adapter.parse(document, 200)

    assert result.status == "blocked"


@pytest.mark.parametrize("adapter", [IndeedAdapter(), LinkedInAdapter()])
def test_adapters_detect_expired_pages(adapter: object) -> None:
    document = (FIXTURES / "vacancy_expired.html").read_text()

    result = adapter.parse(document, 200)

    assert result.status == "expired"


@pytest.mark.parametrize("adapter", [IndeedAdapter(), LinkedInAdapter()])
def test_adapters_detect_empty_descriptions(adapter: object) -> None:
    result = adapter.parse("<html><body><main>Job page</main></body></html>", 200)

    assert result.status == "empty"
