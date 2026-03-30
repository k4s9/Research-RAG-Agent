from pymilvus import MilvusClient
from src.config.settings import settings

# 创建 Milvus 客户端实例
milvus_client = MilvusClient(
    uri=settings.milvus_uri
)

# 集合名称
COLLECTION_NAME = "knowledge_chunks"
