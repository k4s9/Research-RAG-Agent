from typing import List, Dict, Any
import requests
from loguru import logger
from src.config.settings import settings

class Qwen3Embedder:
    def __init__(self):
        self.embedding_url = "http://localhost:8000/v1/embeddings"
    
    def embed(self, texts: List[str]) -> List[List[float]]:
        """获取文本的向量表示"""
        try:
            payload = {
                "input": texts,
                "model": "Qwen/Qwen3-Embedding-0.6B"
            }
            response = requests.post(self.embedding_url, json=payload, timeout=30)
            response.raise_for_status()
            result = response.json()
            embeddings = [item.get("embedding", []) for item in result.get("data", [])]
            logger.info(f"成功获取 {len(embeddings)} 个文本的向量表示")
            return embeddings
        except Exception as e:
            logger.error(f"获取向量表示失败: {str(e)}")
            # 返回空向量作为降级方案
            return [[] for _ in texts]
