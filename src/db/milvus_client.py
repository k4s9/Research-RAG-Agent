from pymilvus import MilvusClient, DataType
from src.config.settings import settings
from loguru import logger

COLLECTION_NAME = "knowledge_chunks"


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
            schema.add_field(
                field_name="entity_type", datatype=DataType.VARCHAR, max_length=16
            )
            schema.add_field(
                field_name="dense_vector", datatype=DataType.FLOAT_VECTOR, dim=1024
            )
            schema.add_field(
                field_name="project_ids",
                datatype=DataType.ARRAY,
                element_type=DataType.VARCHAR,
                max_length=36,
                max_capacity=100,
            )
            schema.add_field(
                field_name="version_status", datatype=DataType.VARCHAR, max_length=16
            )
            schema.add_field(
                field_name="content_type", datatype=DataType.VARCHAR, max_length=16
            )
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

    def insert(self, entities: list):
        """插入向量数据"""
        try:
            result = self.client.insert(
                collection_name=self.collection_name, data=entities
            )
            insert_count = result.get("insert_count", 0)
            logger.info(f"成功插入 {insert_count} 条数据到 Milvus")
            return result
        except Exception as e:
            logger.error(f"插入 Milvus 失败: {str(e)}")
            return {}

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
            logger.info(f"Milvus 搜索完成，返回 {len(result[0])} 条结果")
            return result[0]
        except Exception as e:
            logger.error(f"Milvus 搜索失败: {str(e)}")
            return []

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


try:
    milvus_client = MilvusClientWrapper()
except Exception as e:
    logger.error(f"创建 Milvus 客户端失败: {str(e)}")

    class DummyMilvusClient:
        def insert(self, entities):
            logger.warning("Milvus 连接失败，跳过向量数据插入")
            return {}

        def search(self, vector, top_k=10, filter=None):
            logger.warning("Milvus 连接失败，返回空搜索结果")
            return []

        def has_collection(self, collection_name):
            return False

        def drop_collection(self, collection_name):
            pass

    milvus_client = DummyMilvusClient()
