"""Report contract and deterministic provenance checks (not a semantic judge)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.core.citations import extract_citation_ids


class EvidenceClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: str = Field(min_length=1, max_length=200)
    document_id: str = Field(min_length=1)
    statement: str = Field(min_length=1, max_length=3000)
    verdict: Literal["supported", "contradicted", "insufficient"]
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    # One exact original excerpt per cited source, in the same order.
    quotes: list[str] = Field(default_factory=list, max_length=20)


class ResearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(max_length=4000)
    documents: list[str] = Field(min_length=1, max_length=20)
    dimensions: list[str] = Field(min_length=1, max_length=20)
    claims: list[EvidenceClaim] = Field(min_length=1, max_length=100)
    recommendation: str = Field(max_length=4000)
    # Missing sections are not an affirmative statement that there are no caveats.
    incomparable: list[str] = Field(max_length=30)
    unresolved: list[str] = Field(max_length=30)
    change_summary: str = Field(default="", max_length=2000)


REPORT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_report",
        "description": "Finish with a saved research report. Cite exact original excerpts; insufficient is not false. Every document/dimension needs a row. For verification the statement is the claim being checked, and verdict is the evidence judgment.",
        "parameters": ResearchReport.model_json_schema(),
    },
}
ASK_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": "Pause only for a material ambiguity or missing user constraint.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "minLength": 1, "maxLength": 2000}},
            "required": ["question"],
            "additionalProperties": False,
        },
    },
}


def validate_report(report: ResearchReport, evidence: dict, document_ids=None) -> None:
    if document_ids and set(report.documents) != set(document_ids):
        raise ValueError("report must cover exactly the selected documents")
    missing = {(d, dimension) for d in report.documents for dimension in report.dimensions}
    for claim in report.claims:
        if claim.document_id not in report.documents or claim.dimension not in report.dimensions:
            raise ValueError("claim refers to an undeclared document or dimension")
        missing.discard((claim.document_id, claim.dimension))
        if claim.verdict != "insufficient" and not claim.source_ids:
            raise ValueError("supported/contradicted claims require original evidence")
        if len(claim.source_ids) != len(claim.quotes):
            raise ValueError("provide one exact quote per source_id")
        for sid, quote in zip(claim.source_ids, claim.quotes):
            source = evidence.get(sid)
            if source is None or source.get("document_id") != claim.document_id:
                raise ValueError(f"source {sid} does not belong to the claim document")
            if not quote.strip() or quote not in source["content"]:
                raise ValueError(f"quote is not an exact excerpt of {sid}")
    if missing:
        raise ValueError("missing document/dimension rows; use insufficient for evidence gaps")
    text = "\n".join(
        [
            report.summary,
            report.recommendation,
            *report.incomparable,
            *report.unresolved,
            *(c.statement for c in report.claims),
        ]
    )
    if any(sid not in evidence for sid in extract_citation_ids(text)):
        raise ValueError("report contains unknown source IDs")


def render_report(report: ResearchReport, evidence: dict) -> str:
    def cell(text):
        return text.replace("|", "\\|").replace("\n", "<br>")

    rows = [
        f"# {report.title}",
        "",
        report.summary,
        "",
        "| 维度 | 材料 | 陈述 | 判断 | 出处 |",
        "|---|---|---|---|---|",
    ]
    for c in report.claims:
        filename = next((evidence[s]["filename"] for s in c.source_ids), c.document_id)
        refs = " ".join(f"[{sid}]" for sid in c.source_ids)
        rows.append(
            f"| {cell(c.dimension)} | {cell(filename)} | {cell(c.statement)} | {c.verdict} | {refs} |"
        )
    rows += [
        "",
        "## 推荐与理由",
        "",
        report.recommendation,
        "",
        "## 不可比项",
        "",
        *(report.incomparable or ["无已声明项。"]),
        "",
        "## 未解决问题",
        "",
        *(report.unresolved or ["无已声明项。"]),
    ]
    if report.change_summary:
        rows += ["", "## 修订摘要", "", report.change_summary]
    rows += ["", "## 来源快照", ""]
    used = set(extract_citation_ids("\n".join(rows)))
    for sid, source in evidence.items():
        if sid in used:
            rows += [
                f"- [{sid}] {source['filename']} · {source['locator']} · chunk `{source['chunk_id']}` "
                f"· chars {source['char_start']}:{source['char_end']} · SHA256 `{source['content_hash']}`",
                "",
                "> " + source["content"].replace("\n", "\n> "),
                "",
            ]
    return "\n".join(rows)
