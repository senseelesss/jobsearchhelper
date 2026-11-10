from bs4 import BeautifulSoup

from job_radar.enrichment_adapters.common import (
    ParsedPage,
    description_page,
    structured_metadata,
    classify_common,
    extract_json_ld_description,
    normalize_description,
)


class KarriereAdapter:
    def parse(self, document: str, status_code: int) -> ParsedPage:
        classified = classify_common(document, status_code)
        if classified:
            return classified
        description = extract_json_ld_description(document)
        confidence = "high"
        if not description:
            soup = BeautifulSoup(document, "html.parser")
            body = soup.select_one(
                ".m-jobContent .sja, .m-jobContent__jobContainer, [itemprop=description]"
            )
            if body:
                for element in body.select("script, style, link, button"):
                    element.decompose()
                description = normalize_description(body.get_text("\n", strip=True))
            confidence = "medium"
        if not description:
            return ParsedPage("empty", error="No karriere.at full description found")
        return description_page(description, confidence, structured_metadata(document))
