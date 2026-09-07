"""Small, dependency-free BM25 implementation used for sparse retrieval.

The production vector store owns the corpus and calls this scorer for keyword
search. Keeping the scoring code local makes the offline workflow deterministic
and avoids requiring a second search service just to support BM25.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]", re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    """Tokenize English words and Chinese characters consistently."""

    return TOKEN_PATTERN.findall((text or "").casefold())


class BM25Retriever:
    """Score a collection of result dictionaries with Okapi BM25.

    ``documents`` can be supplied at construction time for a reusable index,
    or passed to :meth:`search` for one-shot scoring. Documents are copied
    before a score is added, so callers never observe accidental mutation.
    """

    def __init__(
        self,
        documents: Iterable[dict[str, Any]] | None = None,
        *,
        k1: float = 1.2,
        b: float = 0.75,
    ) -> None:
        if k1 <= 0:
            raise ValueError("BM25 k1 must be positive")
        if not 0 <= b <= 1:
            raise ValueError("BM25 b must be between 0 and 1")
        self.k1 = k1
        self.b = b
        self._documents = list(documents or [])

    @property
    def documents(self) -> list[dict[str, Any]]:
        return list(self._documents)

    def replace(self, documents: Iterable[dict[str, Any]]) -> None:
        self._documents = list(documents)

    def add(self, documents: Iterable[dict[str, Any]]) -> None:
        self._documents.extend(documents)

    @staticmethod
    def _allowed(
        document: dict[str, Any],
        predicate: Callable[[dict[str, Any]], bool] | None,
    ) -> bool:
        return predicate is None or predicate(document)

    def search(
        self,
        query: str,
        documents: Iterable[dict[str, Any]] | None = None,
        *,
        top_k: int = 10,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        if top_k <= 0:
            return []
        corpus = [
            document
            for document in (self._documents if documents is None else documents)
            if document.get("id") and self._allowed(document, predicate)
        ]
        query_terms = tokenize(query)
        if not corpus or not query_terms:
            return []

        tokenized = [tokenize(str(document.get("content", ""))) for document in corpus]
        lengths = [len(tokens) for tokens in tokenized]
        average_length = sum(lengths) / max(len(lengths), 1)
        document_frequency: Counter[str] = Counter()
        for tokens in tokenized:
            document_frequency.update(set(tokens))

        query_frequency = Counter(query_terms)
        scored: list[dict[str, Any]] = []
        total_documents = len(corpus)
        for document, tokens, length in zip(corpus, tokenized, lengths, strict=True):
            frequencies = Counter(tokens)
            score = 0.0
            for term, query_count in query_frequency.items():
                term_frequency = frequencies.get(term, 0)
                if not term_frequency:
                    continue
                df = document_frequency.get(term, 0)
                # BM25+1 IDF keeps exact matches useful even in tiny corpora.
                idf = math.log(1.0 + (total_documents - df + 0.5) / (df + 0.5))
                normalization = self.k1 * (
                    1.0 - self.b + self.b * length / max(average_length, 1.0)
                )
                score += idf * (
                    term_frequency * (self.k1 + 1.0) / (term_frequency + normalization)
                ) * min(query_count, 2)
            if score <= 0:
                continue
            item = {key: value for key, value in document.items() if key != "dense_vector"}
            item["score"] = score
            item["bm25_score"] = score
            scored.append(item)

        scored.sort(key=lambda item: item["score"], reverse=True)
        for rank, item in enumerate(scored[:top_k], start=1):
            item["rank"] = rank
        return scored[:top_k]
