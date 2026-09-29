from pathlib import Path

import pytest

from src.core.ingest.pdf_chunker import PDFChunker
from src.core.ingest.text_budget import LocalTokenizerBudget

pytestmark = pytest.mark.unit


def test_local_tokenizer_disables_saved_truncation_and_counts_final_chunks(tmp_path: Path):
    tokenizers = pytest.importorskip("tokenizers")
    from tokenizers import models, pre_tokenizers, processors, trainers

    tokenizer = tokenizers.Tokenizer(models.BPE())
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.train_from_iterator(
        ["中文 evidence. Another sentence! " * 20],
        trainers.BpeTrainer(
            vocab_size=300,
            initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),
            special_tokens=["[BOS]", "[EOS]"],
        ),
    )
    tokenizer.post_processor = processors.TemplateProcessing(
        single="[BOS] $A [EOS]",
        special_tokens=[("[BOS]", tokenizer.token_to_id("[BOS]")), ("[EOS]", tokenizer.token_to_id("[EOS]"))],
    )
    tokenizer.enable_truncation(max_length=3)
    tokenizer.enable_padding(length=32)
    path = tmp_path / "tokenizer.json"
    tokenizer.save(str(path))

    budget = LocalTokenizerBudget(str(path))
    text = "中文 evidence. Another sentence! " * 40
    tokenizer.no_truncation()
    tokenizer.no_padding()
    assert budget.count(text) == len(tokenizer.encode(text).ids) > 32
    assert budget.count("x") == len(tokenizer.encode("x").ids) < 32
    chunks = PDFChunker(chunk_size=24, overlap=6, tokenizer_path=str(path)).chunk({
        "file_type": "pdf", "pages": [{"page_num": 1, "text": text}],
    })
    assert all(c.metadata["token_count"] == len(tokenizer.encode(c.content).ids) <= 24 for c in chunks)


def test_configured_missing_tokenizer_does_not_silently_fall_back(tmp_path: Path):
    chunker = PDFChunker(tokenizer_path=str(tmp_path / "missing.json"))
    with pytest.raises(FileNotFoundError):
        chunker.chunk({"file_type": "pdf", "pages": [{"page_num": 1, "text": "evidence"}]})
