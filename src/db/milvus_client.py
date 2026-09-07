from loguru import logger
from pymilvus import DataType, MilvusClient

from src.config.settings import settings
from src.core.retrieval.bm25 import BM25Retriever

COLLECTION_NAME = "knowledge_chunks"
REQUIRED_FIELDS = {
    "id",
    "entity_type",
    "dense_vector",
    "project_ids",
    "version_status",
    "content_type",
    "content",
    "created_at",
}


class MilvusClientWrapper:
    def __init__(self):
        self.client = MilvusClient(uri=settings.milvus_uri)
        self.collection_name = COLLECTION_NAME
        self._ensure_collection_exists()

    def _ensure_collection_exists(self):
        """确保集合存在，不存在则创建"""
        try:
            logger.info(f"检查 Milvus 集合是否存在: {self.collection_name}")
            if self.client.has_collection(collection_name=self.collection_name):
                description = self.client.describe_collection(collection_name=self.collection_name)
                actual_fields = {field["name"] for field in description.get("fields", [])}
                missing_fields = REQUIRED_FIELDS - actual_fields
                if missing_fields:
                    missing = ", ".join(sorted(missing_fields))
                    raise RuntimeError(f"Milvus collection schema 缺少字段: {missing}")
                logger.info(f"Milvus 集合已存在，直接使用: {self.collection_name}")
                return

            logger.info(f"创建 Milvus 集合: {self.collection_name}")

            # 创建 schema
            schema = self.client.create_schema(
                auto_id=False,
                enable_dynamic_field=False,
            )

            # 添加字段
            schema.add_field(
                field_name="id",
                datatype=DataType.VARCHAR,
                max_length=36,
                is_primary=True,
            )
            schema.add_field(field_name="entity_type", datatype=DataType.VARCHAR, max_length=16)
            schema.add_field(
                field_name="dense_vector",
                datatype=DataType.FLOAT_VECTOR,
                dim=settings.embedding_dimension,
            )
            schema.add_field(
                field_name="project_ids",
                datatype=DataType.ARRAY,
                element_type=DataType.VARCHAR,
                max_length=36,
                max_capacity=100,
            )
            schema.add_field(field_name="version_status", datatype=DataType.VARCHAR, max_length=16)
            schema.add_field(field_name="content_type", datatype=DataType.VARCHAR, max_length=16)
            schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=65535)
            schema.add_field(field_name="created_at", datatype=DataType.INT64)

            # 创建索引参数
            index_params = self.client.prepare_index_params()
            index_params.add_index(
                field_name="dense_vector",
                index_type="HNSW",
                metric_type="L2",
                params={"M": 16, "efConstruction": 100},
            )
            index_params.add_index(field_name="project_ids", index_type="INVERTED")
            index_params.add_index(field_name="version_status", index_type="INVERTED")
            index_params.add_index(field_name="content_type", index_type="INVERTED")

            # 创建集合
            logger.info(f"开始创建 Milvus 集合，schema: {schema}")
            self.client.create_collection(
                collection_name=self.collection_name,
                schema=schema,
                index_params=index_params,
            )
            logger.info(f"创建 Milvus 集合成功: {self.collection_name}")
        except Exception as e:
            logger.error(f"确保 Milvus 集合存在失败: {str(e)}")
            import traceback

            logger.error(traceback.format_exc())
            raise

    def ensure_collection(self) -> None:
        self._ensure_collection_exists()

    def recreate_collection(self) -> None:
        """Explicitly recreate the collection for local development/migrations."""
        if self.client.has_collection(collection_name=self.collection_name):
            self.client.drop_collection(collection_name=self.collection_name)
        self._ensure_collection_exists()

    def insert(self, entities: list):
        """插入向量数据"""
        try:
            result = self.client.insert(collection_name=self.collection_name, data=entities)
            insert_count = result.get("insert_count", 0)
            if insert_count != len(entities):
                raise RuntimeError(
                    f"Milvus 插入数量不一致: expected={len(entities)}, actual={insert_count}",
                )
            logger.info(f"成功插入 {insert_count} 条数据到 Milvus")
            return result
        except Exception as e:
            logger.error(f"插入 Milvus 失败: {str(e)}")
            raise

    def delete(self, ids: list[str]):
        """Delete vectors during a compensating cleanup operation."""
        if not ids:
            return
        try:
            quoted_ids = ", ".join(f"'{chunk_id}'" for chunk_id in ids)
            self.client.delete(
                collection_name=self.collection_name,
                filter=f"id in [{quoted_ids}]",
            )
        except Exception as e:
            logger.error(f"删除 Milvus 向量失败: {str(e)}")
            raise

    def search(self, vector: list, top_k: int = 10, filter: str = None):
        """搜索相似向量"""
        try:
            result = self.client.search(
                collection_name=self.collection_name,
                data=[vector],
                limit=top_k,
                filter=filter,
                output_fields=[
                    "id",
                    "entity_type",
                    "content",
                    "project_ids",
                    "version_status",
                    "content_type",
                    "created_at",
                ],
            )
            normalized = []
            for hit in result[0]:
                entity = dict(hit.get("entity", {}))
                entity["id"] = hit.get("id", entity.get("id"))
                entity["distance"] = hit.get("distance", 0.0)
                entity["score"] = 1.0 / (1.0 + max(float(entity["distance"]), 0.0))
                normalized.append(entity)
            logger.info(f"Milvus 搜索完成，返回 {len(normalized)} 条结果")
            return normalized
        except Exception as e:
            logger.error(f"Milvus 搜索失败: {str(e)}")
            raise

    def keyword_search(self, query: str, top_k: int = 10, filter: str = None):
        """Run BM25 over the authoritative text stored in Milvus.

        Milvus remains the source of candidate records and scalar filtering;
        BM25 scoring is performed locally over those records. This keeps the
        same API available for the in-memory development backend while leaving
        room to replace this method with a native sparse index later.
        """
        try:
            limit = max(top_k, settings.sparse_top_k)
            rows = self.client.query(
                collection_name=self.collection_name,
                filter=filter or "",
                output_fields=[
                    "id",
                    "entity_type",
                    "content",
                    "project_ids",
                    "version_status",
                    "content_type",
                    "created_at",
                ],
                limit=limit,
            )
            return BM25Retriever().search(query, rows, top_k=top_k)
        except Exception as exc:
            logger.error(f"Milvus BM25 搜索失败: {exc}")
            raise

    def has_collection(self, collection_name: str):
        """检查集合是否存在"""
        try:
            return self.client.has_collection(collection_name=collection_name)
        except Exception as e:
            logger.error(f"检查 Milvus 集合失败: {str(e)}")
            return False

    def drop_collection(self, collection_name: str):
        """删除集合"""
        try:
            self.client.drop_collection(collection_name=collection_name)
            logger.info(f"删除 Milvus 集合: {collection_name}")
        except Exception as e:
            logger.error(f"删除 Milvus 集合失败: {str(e)}")


class LazyMilvusClient:
    """Create the network client only when a Milvus operation is requested."""

    collection_name = COLLECTION_NAME

    def __init__(self) -> None:
        self._client: MilvusClientWrapper | None = None

    def _get(self) -> MilvusClientWrapper:
        if self._client is None:
            self._client = MilvusClientWrapper()
        return self._client

    def __getattr__(self, name: str):
        return getattr(self._get(), name)


milvus_client = LazyMilvusClient()
