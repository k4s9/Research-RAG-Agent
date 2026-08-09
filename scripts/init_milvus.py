"""Initialize the Milvus collection using the application-owned schema path."""

from src.db.milvus_client import milvus_client


def init_milvus(recreate: bool = False) -> None:
    if recreate:
        milvus_client.recreate_collection()
    else:
        milvus_client.ensure_collection()
    print(f"Milvus collection ready: {milvus_client.collection_name}")


if __name__ == "__main__":
    init_milvus()
