from typing import List, Dict, Any
import requests
from loguru import logger

class Qwen3Reranker:
    def __init__(self):
        self.rerank_url = "http://localhost:8001/rerank"
    
    def rerank(self, query: str, documents: List[str], top_k: int = 5) -> List[Dict[str, Any]]:
        """对文档进行重排序"""
        try:
            payload = {
                "query": query,
                "documents": documents,
                "top_k": top_k
            }
            response = requests.post(self.rerank_url, json=payload, timeout=30)
            response.raise_for_status()
            result = response.json()
            reranked_results = result.get("results", [])
            logger.info(f"成功重排序 {len(reranked_results)} 个文档")
            return reranked_results
        except Exception as e:
            logger.error(f"重排序失败: {str(e)}")
            # 失败时返回原始文档列表
            return [{
                "index": i,
                "document": doc,
                "score": 1.0 - i/len(documents)
            } for i, doc in enumerate(documents)]
