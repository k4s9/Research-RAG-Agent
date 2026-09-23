import pytest

from src.db.milvus_client import MilvusClientWrapper, REQUIRED_FIELDS
from src.config.settings import settings

pytestmark = pytest.mark.unit


class FakeMilvusClient:
    def __init__(self, fields: set[str]) -> None:
        self.fields = fields

    def has_collection(self, collection_name: str) -> bool:
        return True

    def describe_collection(self, collection_name: str) -> dict:
        return {"fields": [{"name": field, "params": {"dim": settings.embedding_dimension}}
                           for field in self.fields]}


def wrapper_with_client(client: FakeMilvusClient) -> MilvusClientWrapper:
    wrapper = object.__new__(MilvusClientWrapper)
    wrapper.client = client
    wrapper.collection_name = "knowledge_chunks"
    return wrapper


def test_existing_milvus_collection_requires_authoritative_schema() -> None:
    wrapper = wrapper_with_client(FakeMilvusClient(REQUIRED_FIELDS - {"content"}))

    with pytest.raises(RuntimeError, match="content"):
        wrapper.ensure_collection()


def test_existing_milvus_collection_accepts_authoritative_schema() -> None:
    wrapper = wrapper_with_client(FakeMilvusClient(REQUIRED_FIELDS))

    wrapper.ensure_collection()


def test_existing_milvus_collection_rejects_wrong_dimension() -> None:
    client = FakeMilvusClient(REQUIRED_FIELDS)
    client.describe_collection = lambda **kwargs: {"fields": [
        {"name": field, "params": {"dim": settings.embedding_dimension + 1}} for field in REQUIRED_FIELDS]}
    with pytest.raises(RuntimeError, match="dimension"):
        wrapper_with_client(client).ensure_collection()
