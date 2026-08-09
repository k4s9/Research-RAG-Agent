from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from src.api.documents import _write_upload
from src.config.settings import settings

pytestmark = pytest.mark.unit


def test_write_upload_rejects_invalid_pdf_signature(tmp_path: Path) -> None:
    upload = UploadFile(filename="paper.pdf", file=BytesIO(b"not a pdf"))
    destination = tmp_path / "upload" / "paper.pdf"
    destination.parent.mkdir()

    with pytest.raises(HTTPException, match="PDF 文件签名无效"):
        _write_upload(upload, destination, ".pdf")

    assert not destination.exists()


def test_write_upload_enforces_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_upload_size_bytes", 4)
    upload = UploadFile(filename="notes.md", file=BytesIO(b"12345"))
    destination = tmp_path / "upload" / "notes.md"
    destination.parent.mkdir()

    with pytest.raises(HTTPException) as exc_info:
        _write_upload(upload, destination, ".md")

    assert exc_info.value.status_code == 413
    assert not destination.exists()
