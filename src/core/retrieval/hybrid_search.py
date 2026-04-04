from typing import List, Dict, Any, Optional
from loguru import logger
from src.core.retrieval.embedder import Qwen3Embedder
from src.db.milvus_client import milvus_client
from src.config.settings import settings

class HybridSearch:
    def __init__(self):
        self.embedder = Qwen3Embedder()
    
    def search(self, query: str, project_ids: List[str], top_k: int = 5, include_outdated: bool = False, content_types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """执行混合检索"""
        try:
            # 1. 获取查询向量
            query_embedding = self.embedder.embed([query])[0]
            if not query_embedding:
                logger.warning("查询向量获取失败，返回空结果")
                return []
            
            # 2. 构建过滤条件
            filters = []
            if not include_outdated:
                filters.append("version_status == 'active'")
            if project_ids:
                # Milvus ARRAY 字段使用 ARRAY_CONTAINS 操作符
                project_filters = []
                for pid in project_ids:
                    project_filters.append(f"ARRAY_CONTAINS(project_ids, '{pid}')")
                filters.append("(" + " || ".join(project_filters) + ")")
            if content_types:
                content_filter = " || ".join([f"content_type == '{ct}'" for ct in content_types])
                filters.append(f"({content_filter})")
            
            filter_expr = " && ".join(filters) if filters else None
            
            # 3. 执行 Dense 检索
            dense_results = milvus_client.search(
                vector=query_embedding,
                top_k=settings.dense_top_k,
                filter=filter_expr
            )
            
            # 4. 应用时间衰减
            from src.core.retrieval.time_decay import apply_time_decay
            decay_results = apply_time_decay(dense_results)
            
            # 5. 排序并取 Top-K
            decay_results.sort(key=lambda x: x.get("score", 0), reverse=True)
            final_results = decay_results[:top_k]
            
            logger.info(f"混合检索完成，返回 {len(final_results)} 条结果")
            return final_results
            
        except Exception as e:
            logger.error(f"混合检索失败: {str(e)}")
            return []
