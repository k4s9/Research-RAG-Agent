from fastapi import APIRouter, HTTPException
from src.schemas.search import SearchRequest, SearchResponse
from src.core.retrieval.hybrid_search import HybridSearch
from loguru import logger
import time

router = APIRouter()

@router.post("", response_model=SearchResponse)
async def search(request: SearchRequest):
    """执行搜索"""
    try:
        start_time = time.time()
        
        # 初始化搜索器
        searcher = HybridSearch()
        
        # 执行搜索
        results = searcher.search(
            query=request.query,
            project_ids=request.project_ids,
            top_k=request.top_k,
            include_outdated=request.include_outdated,
            content_types=request.content_types
        )
        
        # 构建响应
        search_results = []
        for result in results:
            search_result = {
                "id": result.get("id"),
                "entity_type": result.get("entity_type"),
                "content": "",  # 这里需要从数据库中获取完整内容
                "content_type": result.get("content_type"),
                "version_status": result.get("version_status"),
                "source": "",  # 这里需要从数据库中获取来源信息
                "score": result.get("score", 0),
                "project_ids": result.get("project_ids", []),
                "created_at": ""  # 这里需要从数据库中获取创建时间
            }
            search_results.append(search_result)
        
        query_time_ms = int((time.time() - start_time) * 1000)
        
        logger.info(f"搜索完成: 查询='{request.query}', 结果数={len(search_results)}, 耗时={query_time_ms}ms")
        
        return SearchResponse(
            results=search_results,
            total=len(search_results),
            query_time_ms=query_time_ms
        )
        
    except Exception as e:
        logger.error(f"搜索失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"搜索失败: {str(e)}")

