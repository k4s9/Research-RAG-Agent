from typing import List, Dict, Any
from src.config.settings import settings


def rrf_fusion(results_list: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """使用 RRF (Reciprocal Rank Fusion) 融合多个检索结果列表"""
    # 存储每个文档的 RRF 分数
    doc_scores = {}
    
    # 对每个结果列表计算 RRF 分数
    for results in results_list:
        for rank, result in enumerate(results):
            doc_id = result.get("id")
            if doc_id:
                # RRF 公式: 1 / (k + rank)
                score = 1.0 / (settings.rrf_k + rank + 1)  # rank 从 0 开始，所以 +1
                doc_scores[doc_id] = doc_scores.get(doc_id, 0) + score
    
    # 按分数排序
    sorted_docs = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)
    
    # 构建融合后的结果列表
    fused_results = []
    for doc_id, score in sorted_docs:
        # 查找原始结果中的文档信息
        for results in results_list:
            for result in results:
                if result.get("id") == doc_id:
                    # 复制原始结果并更新分数
                    fused_result = result.copy()
                    fused_result["score"] = score
                    fused_results.append(fused_result)
                    break
            else:
                continue
            break
    
    return fused_results
