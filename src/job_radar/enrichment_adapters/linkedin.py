from job_radar.enrichment_adapters.common import (
    ParsedPage,
    description_page,
    structured_metadata,
    classify_common,
    extract_element_text,
    extract_json_ld_description,
)


class LinkedInAdapter:
    source = "linkedin"

    def parse(self, document: str, status_code: int) -> ParsedPage:
        classified = classify_common(document, status_code)
        if classified:
            return classified

        description = extract_json_ld_description(document)
        confidence = "high"
        if not description:
            description = extract_element_text(
                document,
                "class",
                {"show-more-less-html__markup", "description__text"},
            )
            confidence = "medium"
        if not description:
            return ParsedPage("empty", error="LinkedIn description was empty")
        return description_page(description, confidence, structured_metadata(document))
