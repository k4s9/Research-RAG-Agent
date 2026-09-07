import json
import os

import fitz
import pytest
from fastapi.testclient import TestClient

from src.main import app

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_E2E") != "1",
        reason="set RUN_LIVE_E2E=1 after configuring all live services",
    ),
]


def _pdf_bytes(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    payload = document.tobytes()
    document.close()
    return payload


def test_live_four_document_ingestion_search_and_answer() -> None:
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "Live E2E"})
        assert project.status_code == 201, project.text
        project_id = project.json()["id"]
        fixtures = [
            (
                "live-a.pdf",
                _pdf_bytes("Live alpha service contract is verified."),
                "application/pdf",
            ),
            (
                "live-b.pdf",
                _pdf_bytes("Live beta service contract is verified."),
                "application/pdf",
            ),
            ("live-a.md", b"# Evidence\n\nLive gamma contract is verified.\n", "text/markdown"),
            (
                "live-b.markdown",
                b"# Evidence\n\nLive delta contract is verified.\n",
                "text/markdown",
            ),
        ]
        for filename, content, mime_type in fixtures:
            upload = client.post(
                "/api/v1/documents/upload",
                files={"file": (filename, content, mime_type)},
                data={"project_ids": json.dumps([project_id])},
            )
            assert upload.status_code == 200, upload.text
            assert upload.json()["status"] == "ready"
        search = client.post(
            "/api/v1/search",
            json={"query": "live service contract", "project_ids": [project_id]},
        )
        assert search.status_code == 200, search.text
        assert search.json()["results"]
        chat = client.post(
            "/api/v1/chat/message",
            json={
                "session_id": "live-e2e",
                "project_ids": [project_id],
                "message": "What is verified?",
            },
        )
        assert chat.status_code == 200, chat.text
        assert not chat.json()["invalid_citation_ids"]
