from typing import List, Dict, Any
import math
from datetime import datetime
from src.config.settings import settings


def apply_time_decay(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """对检索结果应用时间衰减加权"""
    current_time = datetime.utcnow().timestamp()
    decay_results = []
    
    for result in results:
        # 获取创建时间
        created_at = result.get("created_at", current_time)
        # 计算时间差（天）
        delta_days = (current_time - created_at) / (24 * 3600)
        # 应用时间衰减公式
        decay_factor = settings.time_decay_lambda + (1 - settings.time_decay_lambda) * math.exp(-settings.time_decay_alpha * delta_days)
        # 获取原始分数（如果没有分数，使用距离的倒数作为分数）
        original_score = result.get("score", 0)
        if not original_score and "distance" in result:
            # 距离越小，相似度越高，所以取倒数
            original_score = 1.0 / (result["distance"] + 1e-10)
        # 计算衰减后的分数
        decay_score = original_score * decay_factor
        # 更新结果
        result["score"] = decay_score
        result["decay_factor"] = decay_factor
        decay_results.append(result)
    
    return decay_results
