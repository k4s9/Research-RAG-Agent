"""Explicit chunk sizing, with optional local embedding-model tokenization."""

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol


class TextBudget(Protocol):
    unit: str
    identity: str

    def count(self, text: str) -> int: ...


class UTF8Budget:
    """A byte budget, deliberately not reported as measured model tokens."""

    unit = "utf8_bytes"
    identity = "utf8-v1"

    def count(self, text: str) -> int:
        return len(text.encode("utf-8"))


@lru_cache(maxsize=4)
def _load_tokenizer(path: str, digest: str) -> Any:
    del digest  # Included in the cache key so a replaced file is reloaded.
    try:
        from tokenizers import Tokenizer
    except ImportError as exc:
        raise RuntimeError("PDF tokenizer 需要安装项目的 tokenization 可选依赖") from exc
    tokenizer = Tokenizer.from_file(path)
    # Counting must never use the tokenizer file's truncation or padding policy.
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer


class LocalTokenizerBudget:
    unit = "tokens"

    def __init__(self, path: str) -> None:
        source = Path(path).expanduser().resolve()
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.identity = f"tokenizer-json-sha256:{digest}"
        self.tokenizer = _load_tokenizer(str(source), digest)

    def count(self, text: str) -> int:
        return len(self.tokenizer.encode(text, add_special_tokens=True).ids)
