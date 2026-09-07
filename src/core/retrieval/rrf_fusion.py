from typing import Any

from src.config.settings import settings


def rrf_fusion(
    results_list: list[list[dict[str, Any]]],
    *,
    k: int | None = None,
) -> list[dict[str, Any]]:
    """Fuse ranked result lists while retaining per-channel trace metadata.

    RRF deliberately combines ranks rather than raw scores, since BM25 and
    vector similarity are not calibrated to the same range. Duplicate records
    are represented once and retain the best payload seen across channels.
    """

    rrf_k = settings.rrf_k if k is None else k
    if rrf_k < 0:
        raise ValueError("RRF k must be non-negative")

    by_id: dict[str, dict[str, Any]] = {}
    scores: dict[str, float] = {}
    channels: dict[str, set[str]] = {}
    ranks: dict[str, dict[str, int]] = {}
    for channel_index, results in enumerate(results_list):
        if channel_index == 0:
            channel = "dense"
        elif channel_index == 1:
            channel = "bm25"
        else:
            channel = f"channel_{channel_index}"
        for zero_based_rank, result in enumerate(results):
            doc_id = result.get("id")
            if not doc_id:
                continue
            rank = zero_based_rank + 1
            if doc_id not in by_id:
                by_id[doc_id] = dict(result)
            else:
                for key in ("dense_score", "bm25_score", "distance"):
                    if key in result:
                        by_id[doc_id][key] = result[key]
            channels.setdefault(doc_id, set()).add(channel)
            ranks.setdefault(doc_id, {})[f"{channel}_rank"] = rank
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank)

    fused: list[dict[str, Any]] = []
    for doc_id, rrf_score in sorted(scores.items(), key=lambda item: item[1], reverse=True):
        item = by_id[doc_id]
        item["score"] = rrf_score
        item["rrf_score"] = rrf_score
        item["retrieval_channels"] = sorted(channels[doc_id])
        item.update(ranks[doc_id])
        fused.append(item)
    for rank, item in enumerate(fused, start=1):
        item["rrf_rank"] = rank
    return fused
