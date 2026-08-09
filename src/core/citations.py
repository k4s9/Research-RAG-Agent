import re
from typing import Any


SOURCE_ID_PATTERN = re.compile(r"\[S(\d+)\]")


def source_locator(
    filename: str, file_type: str, metadata: dict[str, Any] | None
) -> dict[str, Any]:
    metadata = metadata or {}
    if file_type == "pdf":
        return {
            "file_type": "pdf",
            "page_start": metadata.get("page_start", metadata.get("page_num")),
            "page_end": metadata.get("page_end", metadata.get("page_num")),
        }
    return {
        "file_type": "markdown",
        "section_path": metadata.get("section_path", []),
        "line_start": metadata.get("section_start_line"),
        "line_end": metadata.get("section_end_line"),
    }


def extract_citation_ids(answer: str) -> list[str]:
    return list(dict.fromkeys(f"S{match}" for match in SOURCE_ID_PATTERN.findall(answer)))


def validate_citation_ids(answer: str, sources: dict[str, dict[str, Any]]) -> list[str]:
    return [source_id for source_id in extract_citation_ids(answer) if source_id in sources]
