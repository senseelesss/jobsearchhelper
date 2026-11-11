"""First-party JSON-LD and source-specific server-rendered description fallback."""

from bs4 import BeautifulSoup

from job_radar.enrichment_adapters.common import (
    ParsedPage,
    classify_common,
    description_page,
    extract_json_ld_description,
    normalize_description,
    structured_metadata,
)


class StructuredAdapter:
    def parse(self, document: str, status_code: int) -> ParsedPage:
        classified = classify_common(document, status_code)
        if classified:
            return classified
        description = extract_json_ld_description(document)
        confidence = "high"
        if not description:
            soup = BeautifulSoup(document, "html.parser")
            container = soup.select_one('.job_description, [itemprop="description"]')
            if container:
                for tag in container.select("script, style, nav, button"):
                    tag.decompose()
                description = normalize_description(container.get_text("\n", strip=True))
            confidence = "medium"
        return description_page(description, confidence, structured_metadata(document))
