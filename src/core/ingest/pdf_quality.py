"""Page accounting shared by PDF ingestion and its public failure response."""

from typing import Any

PDF_INGEST_VERSION = "native-v2"


class PDFQualityError(ValueError):
    def __init__(self, page_results: list[dict[str, Any]]) -> None:
        self.page_results = page_results
        self.document_id: str | None = None
        blocked = [str(page["page_num"]) for page in page_results if page["status"] not in {"ok", "blank"}]
        super().__init__(
            f"PDF 第 {', '.join(blocked)} 页需要 OCR 或人工复核，未完成入库。"
            "请提供带可用文字层的 PDF，或在支持 OCR 后重新处理。",
        )


def pdf_page_results(
    parsed: dict[str, Any], cleaned: dict[str, Any],
) -> list[dict[str, Any]]:
    cleaned_pages = {page["page_num"]: page for page in cleaned.get("pages", [])}
    results = []
    for index, page in enumerate(parsed.get("pages", [])):
        number = page.get("page_num", index + 1)
        clean_page = cleaned_pages.get(number, {})
        original_text = page.get("text", "").strip()
        clean_text = clean_page.get("text", "").strip()
        status = page.get("parse_status")
        reason = page.get("parse_reason")
        if status is None:
            # Third-party parsers must explicitly identify blank pages. Empty
            # extraction alone does not establish that a page is blank.
            status = "ok" if original_text else "needs_review"
            reason = None if original_text else "unclassified_empty_page"
        if status == "ok" and (not original_text or not clean_text):
            status, reason = "needs_review", "empty_after_cleaning"
        if status == "blank" and original_text:
            status, reason = "needs_review", "inconsistent_blank_page"
        results.append({
            "page_num": number,
            "status": status,
            "reason": reason,
            "extraction_method": page.get("extraction_method", "unknown"),
            "text_char_count": len(original_text),
            "cleaned_char_count": len(clean_text),
            "removed_line_count": len(clean_page.get("cleaning", {}).get("removed_lines", [])),
            "warnings": clean_page.get("warnings", page.get("warnings", [])),
            "chunk_count": 0,
        })
    return results
