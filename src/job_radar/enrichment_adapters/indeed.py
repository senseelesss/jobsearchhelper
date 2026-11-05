from job_radar.enrichment_adapters.common import (
    ParsedPage,
    classify_common,
    description_language,
    extract_element_text,
    extract_json_ld_description,
)


class IndeedAdapter:
    source = "indeed"

    def parse(self, document: str, status_code: int) -> ParsedPage:
        classified = classify_common(document, status_code)
        if classified:
            return classified

        description = extract_json_ld_description(document)
        confidence = "high"
        if not description:
            description = extract_element_text(
                document, "id", {"jobDescriptionText"}
            )
            confidence = "medium"
        if not description:
            return ParsedPage("empty", error="Indeed description was empty")
        return ParsedPage(
            "success",
            description=description,
            description_language=description_language(description),
            description_confidence=confidence,
        )
