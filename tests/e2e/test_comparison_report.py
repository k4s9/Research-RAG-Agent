"""Real local ingestion/retrieval/reading/report persistence; scripted model only."""

import json

import httpx
import pytest
from test_offline_workflow import tool_call, tool_envelope
from test_offline_workflow import offline_services as offline_services

pytestmark = pytest.mark.e2e


class ComparingModel:
    def __init__(self):
        self.step = 0
        self.document_ids = []
        self.observed_ids = []

    def generate_with_tools(self, prompt, **kwargs):
        messages = kwargs["tool_messages"]
        self.step += 1
        if self.step == 1:
            return tool_envelope([tool_call("discover", "list_documents", {})])
        if self.step == 2:
            documents = json.loads(messages[-1]["content"])["documents"]
            self.document_ids = [d["id"] for d in documents]
            return tool_envelope(
                [
                    tool_call(
                        "locate", "search_knowledge", {"query": "memory requirements", "top_k": 5}
                    )
                ]
            )
        if self.step == 3:
            found = json.loads(messages[-1]["content"])["results"]
            self.observed_ids = list(dict.fromkeys(r["document_id"] for r in found))
            return tool_envelope(
                [
                    tool_call(str(i), "read_document_range", {"document_id": d, "max_chars": 2000})
                    for i, d in enumerate(self.observed_ids)
                ]
            )
        reads = [
            json.loads(m["content"])
            for m in messages
            if m["role"] == "tool" and "chunks" in json.loads(m["content"])
        ]
        claims = []
        for read in reads:
            for chunk in read["chunks"][:1]:
                claims.append(
                    dict(
                        dimension="memory",
                        document_id=read["document_id"],
                        statement=chunk["content"],
                        verdict="supported",
                        source_ids=[chunk["source_id"]],
                        quotes=[chunk["content"]],
                    )
                )
        return tool_envelope(
            [
                tool_call(
                    "report",
                    "submit_report",
                    dict(
                        title="Memory report",
                        summary="Compare reported memory.",
                        documents=self.document_ids,
                        dimensions=["memory"],
                        claims=claims,
                        recommendation="Use the stated memory limits; no cross-dataset ranking.",
                        incomparable=["Different datasets."],
                        unresolved=[],
                    ),
                )
            ]
        )


@pytest.mark.asyncio
async def test_ingest_compare_read_save_reopen(offline_services):
    from src.main import app

    offline_services.orchestrator.llm_client = ComparingModel()
    model = offline_services.orchestrator.llm_client
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        project = (
            await client.post("/api/v1/projects", json={"name": "Comparison corpus"})
        ).json()["id"]
        for name, content in [
            ("A.md", "# Alpha\n\nMemory requirements: 8 GB on Dataset X."),
            ("B.md", "# Beta\n\nMemory requirements: 16 GB on Dataset Y."),
        ]:
            upload = await client.post(
                "/api/v1/ documents/upload".replace(" ", ""),
                files={"file": (name, content.encode(), "text/markdown")},
                data={"project_ids": json.dumps([project])},
            )
            assert upload.status_code == 200, upload.text
        answer = await client.post(
            "/api/v1/chat/message",
            json=dict(
                session_id="comparison",
                project_ids=[project],
                message="Compare the memory requirements and comparability of A and B.",
                strategy="b2",
                task_type="compare",
            ),
        )
        assert answer.status_code == 200, answer.text
        result = answer.json()
        assert result["run_status"] == "completed", result
        assert set(model.observed_ids) == set(model.document_ids)
        assert result["invalid_citation_ids"] == []
        artifact = (await client.get("/api/v1/chat/reports/" + result["report_id"])).json()
        assert len(artifact["structured"]["claims"]) == 2
        for claim in artifact["structured"]["claims"]:
            source = artifact["evidence"][claim["source_ids"][0]]
            assert source["document_id"] == claim["document_id"]
            assert source["document_hash"]
            assert claim["quotes"][0] in source["content"]
        downloaded = await client.get("/api/v1/chat/reports/" + result["report_id"] + "/download")
        assert downloaded.text == artifact["markdown"]
