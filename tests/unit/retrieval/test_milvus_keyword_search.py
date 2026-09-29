import pytest

from src.config.settings import settings
from src.db.milvus_client import MilvusClientWrapper
from src.db.vector_store import InMemoryVectorStore

pytestmark = pytest.mark.unit


class PageIterator:
    def __init__(self, rows, batch_size):
        self.rows = rows
        self.batch_size = batch_size
        self.position = 0
        self.closed = False

    def next(self):
        batch = self.rows[self.position:self.position + self.batch_size]
        self.position += len(batch)
        return batch

    def close(self):
        self.closed = True


class CorpusClient:
    def __init__(self, rows):
        self.rows = rows
        self.iterator = None
        self.iterator_arguments = None

    def matching(self, expression):
        return [row for row in self.rows if InMemoryVectorStore._matches(row, expression)]

    def query(self, *, filter, limit, **kwargs):
        return self.matching(filter)[:limit]

    def query_iterator(self, *, filter, batch_size, **kwargs):
        self.iterator_arguments = {"filter": filter, "batch_size": batch_size, **kwargs}
        self.iterator = PageIterator(self.matching(filter), min(batch_size, 13))
        return self.iterator


def wrapper(client):
    result = object.__new__(MilvusClientWrapper)
    result.client = client
    result.collection_name = "test-corpus"
    return result


def test_milvus_bm25_searches_past_first_top_k_page_and_respects_scope():
    rows = [
        {"id": f"noise-{i}", "content": "unrelated background", "project_ids": ["p"],
         "version_status": "active"}
        for i in range(settings.sparse_top_k + 9)
    ]
    rows.extend([
        {"id": "target", "content": "rare calibration", "project_ids": ["p"],
         "version_status": "active"},
        {"id": "private", "content": "rare calibration", "project_ids": ["other"],
         "version_status": "active"},
        {"id": "old", "content": "rare calibration", "project_ids": ["p"],
         "version_status": "outdated"},
    ])
    client = CorpusClient(rows)
    expression = "ARRAY_CONTAINS(project_ids, 'p') && version_status == 'active'"

    hits = wrapper(client).keyword_search("rare calibration", top_k=1, filter=expression)

    assert [hit["id"] for hit in hits] == ["target"]
    assert client.iterator.closed
    assert client.iterator_arguments["filter"] == expression
    assert client.iterator_arguments["limit"] == -1
