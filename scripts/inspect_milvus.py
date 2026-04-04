#!/usr/bin/env python3
"""Milvus 数据库可视化与验证脚本"""

from pymilvus import MilvusClient
from src.config.settings import settings
import json


class MilvusInspector:
    def __init__(self, uri: str = None):
        self.uri = uri or settings.milvus_uri
        self.client = MilvusClient(uri=self.uri)
        self.collection_name = "knowledge_chunks"

    def print_header(self, title: str):
        print(f"\n{'='*60}")
        print(f"  {title}")
        print('='*60)

    def check_connection(self) -> bool:
        try:
            self.client.list_collections()
            print("[✓] Milvus 连接成功")
            return True
        except Exception as e:
            print(f"[✗] Milvus 连接失败: {e}")
            return False

    def show_schema(self):
        self.print_header("Collection Schema 信息")

        if not self.client.has_collection(self.collection_name):
            print(f"[!] Collection '{self.collection_name}' 不存在")
            return

        schema = self.client.describe_collection(self.collection_name)
        print(f"\nCollection 名称: {schema.get('collection_name')}")
        print(f"说明: {schema.get('description', 'N/A')}")

        print("\n字段定义:")
        for field in schema.get('fields', []):
            field_info = f"  - {field['name']}: {field['type']}"
            if field.get('type') == "Array":
                field_info += f"<{field.get('element_type')}>"
                field_info += f" (max_capacity: {field.get('max_capacity', 'N/A')})"
            elif field.get('type') == "VarChar":
                field_info += f" (max_length: {field.get('max_length', 'N/A')})"
            elif field.get('type') == "FloatVector":
                field_info += f" (dim: {field.get('dim', 'N/A')})"
            if field.get('is_primary'):
                field_info += " [PK]"
            print(field_info)

        print("\n索引信息:")
        indexes = schema.get('indexes', []) or []
        for idx in indexes:
            print(f"  - {idx.get('field_name')}: {idx.get('index_type')} ({idx.get('metric_type')})")

    def show_stats(self):
        self.print_header("Collection 统计信息")

        if not self.client.has_collection(self.collection_name):
            return

        stats = self.client.get_collection_stats(self.collection_name)
        print(f"\n总实体数: {stats.get('total_entity_count', 0)}")
        print(f"分区数: {stats.get('partitions_count', 0)}")

        # 按 entity_type 统计
        print("\n按 entity_type 统计:")
        type_counts = {}
        version_counts = {}
        content_type_counts = {}

        # 查询样本数据用于统计
        results = self.client.query(
            collection_name=self.collection_name,
            filter="",
            output_fields=["entity_type", "version_status", "content_type"],
            limit=1000
        )

        for item in results:
            et = item.get('entity_type', 'unknown')
            vs = item.get('version_status', 'unknown')
            ct = item.get('content_type', 'unknown')
            type_counts[et] = type_counts.get(et, 0) + 1
            version_counts[vs] = version_counts.get(vs, 0) + 1
            content_type_counts[ct] = content_type_counts.get(ct, 0) + 1

        print(f"  - entity_type: {json.dumps(type_counts, ensure_ascii=False)}")
        print(f"  - version_status: {json.dumps(version_counts, ensure_ascii=False)}")
        print(f"  - content_type: {json.dumps(content_type_counts, ensure_ascii=False)}")

    def show_sample_data(self, limit: int = 5):
        self.print_header(f"样本数据 (前 {limit} 条)")

        if not self.client.has_collection(self.collection_name):
            return

        results = self.client.query(
            collection_name=self.collection_name,
            filter="",
            output_fields=["id", "entity_type", "content", "project_ids", "version_status", "content_type", "created_at"],
            limit=limit
        )

        if not results:
            print("\n[!] Collection 中没有数据")
            return

        for i, item in enumerate(results, 1):
            print(f"\n--- 记录 {i} ---")
            print(f"ID: {item.get('id')}")
            print(f"类型: {item.get('entity_type')}")
            print(f"内容类型: {item.get('content_type')}")
            print(f"版本状态: {item.get('version_status')}")
            print(f"项目IDs: {item.get('project_ids', [])}")
            print(f"创建时间: {item.get('created_at')} ({self._format_timestamp(item.get('created_at'))})")

            content = item.get('content', '')
            if len(content) > 300:
                content = content[:300] + "..."
            print(f"内容预览:\n{item.get('content', 'N/A')}")

    def verify_chunk_size(self, sample_size: int = 100):
        self.print_header("分块大小验证")

        if not self.client.has_collection(self.collection_name):
            return

        results = self.client.query(
            collection_name=self.collection_name,
            filter='entity_type == "chunk"',
            output_fields=["id", "content", "content_type"],
            limit=sample_size
        )

        if not results:
            print("\n[!] 没有找到 chunk 类型的数据")
            return

        char_counts = [len(item.get('content', '')) for item in results]

        print(f"\n分析的 chunk 数量: {len(char_counts)}")
        print(f"字符数统计:")
        print(f"  - 最小: {min(char_counts)}")
        print(f"  - 最大: {max(char_counts)}")
        print(f"  - 平均: {sum(char_counts) / len(char_counts):.1f}")
        print(f"  - 中位数: {sorted(char_counts)[len(char_counts) // 2]}")

        # 理想 chunk 大小范围 (512-1024 tokens ≈ 2000-4000 字符)
        ideal_min, ideal_max = 200, 4000
        ideal_count = sum(1 for c in char_counts if ideal_min <= c <= ideal_max)
        print(f"\n理想范围 (200-4000 字符) 的 chunk: {ideal_count}/{len(char_counts)} ({ideal_count/len(char_counts)*100:.1f}%)")

    def _format_timestamp(self, ts: int) -> str:
        if not ts:
            return "N/A"
        from datetime import datetime
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')

    def show_all(self):
        print("\n" + "="*60)
        print("  Milvus 数据库可视化与验证报告")
        print("="*60)
        print(f"\n连接地址: {self.uri}")
        print(f"Collection: {self.collection_name}")

        if not self.check_connection():
            return

        self.show_schema()
        self.show_stats()
        self.show_sample_data(limit=5)
        self.verify_chunk_size(sample_size=100)

        print("\n" + "="*60)
        print("  报告生成完成")
        print("="*60)


if __name__ == "__main__":
    inspector = MilvusInspector()
    inspector.show_all()

# /home/guozy/miniconda3/envs/research_rag/bin/python scripts/inspect_milvus.py