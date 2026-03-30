from src.db.milvus_client import milvus_client, COLLECTION_NAME

# 集合模式定义
collection_schema = {
    "collection_name": COLLECTION_NAME,
    "dimension": 1024,  # BGE-M3 dense vector 维度
    "primary_field": "id",
    "auto_id": False,
    "fields": [
        {
            "name": "id",
            "type": "VARCHAR",
            "max_length": 36,
            "is_primary": True
        },
        {
            "name": "entity_type",
            "type": "VARCHAR",
            "max_length": 16
        },
        {
            "name": "dense_vector",
            "type": "FLOAT_VECTOR",
            "dimension": 1024
        },
        {
            "name": "sparse_vector",
            "type": "SPARSE_FLOAT_VECTOR"
        },
        {
            "name": "project_ids",
            "type": "ARRAY",
            "element_type": "VARCHAR",
            "max_length": 36
        },
        {
            "name": "version_status",
            "type": "VARCHAR",
            "max_length": 16
        },
        {
            "name": "content_type",
            "type": "VARCHAR",
            "max_length": 16
        },
        {
            "name": "created_at",
            "type": "INT64"
        }
    ]
}

def init_milvus():
    """初始化 Milvus 集合"""
    # 检查集合是否存在
    if milvus_client.has_collection(COLLECTION_NAME):
        # 删除现有集合
        milvus_client.drop_collection(COLLECTION_NAME)
        print(f"已删除现有集合: {COLLECTION_NAME}")
    
    # 创建新集合
    milvus_client.create_collection(
        collection_name=COLLECTION_NAME,
        dimension=1024,
        primary_field_name="id",
        auto_id=False
    )
    
    # 添加向量字段
    milvus_client.add_field(
        collection_name=COLLECTION_NAME,
        field_name="dense_vector",
        field_type="FLOAT_VECTOR",
        dimension=1024
    )
    
    milvus_client.add_field(
        collection_name=COLLECTION_NAME,
        field_name="sparse_vector",
        field_type="SPARSE_FLOAT_VECTOR"
    )
    
    # 添加标量字段
    milvus_client.add_field(
        collection_name=COLLECTION_NAME,
        field_name="entity_type",
        field_type="VARCHAR",
        max_length=16
    )
    
    milvus_client.add_field(
        collection_name=COLLECTION_NAME,
        field_name="project_ids",
        field_type="ARRAY",
        element_type="VARCHAR",
        max_length=36
    )
    
    milvus_client.add_field(
        collection_name=COLLECTION_NAME,
        field_name="version_status",
        field_type="VARCHAR",
        max_length=16
    )
    
    milvus_client.add_field(
        collection_name=COLLECTION_NAME,
        field_name="content_type",
        field_type="VARCHAR",
        max_length=16
    )
    
    milvus_client.add_field(
        collection_name=COLLECTION_NAME,
        field_name="created_at",
        field_type="INT64"
    )
    
    # 创建索引
    milvus_client.create_index(
        collection_name=COLLECTION_NAME,
        field_name="dense_vector",
        index_type="IVF_FLAT",
        metric_type="L2",
        params={"nlist": 128}
    )
    
    milvus_client.create_index(
        collection_name=COLLECTION_NAME,
        field_name="sparse_vector",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="IP"
    )
    
    print(f"Milvus 集合 {COLLECTION_NAME} 初始化完成")

if __name__ == "__main__":
    init_milvus()
