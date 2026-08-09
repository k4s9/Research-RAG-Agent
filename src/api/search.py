from fastapi import APIRouter, HTTPException
from src.schemas.search import SearchRequest, SearchResponse
from src.core.retrieval.hybrid_search import HybridSearch
from src.core.retrieval.hydration import hydrate_search_results
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
            content_types=request.content_types,
        )

        # Milvus is an index only; hydrate authoritative text and locators from PostgreSQL.
        hydrated_results = await hydrate_search_results(results, request.project_ids)
        search_results = []
        for result in hydrated_results:
            search_result = {
                "id": result["id"],
                "entity_type": result.get("entity_type"),
                "content": result["content"],
                "content_type": result["content_type"],
                "version_status": result["version_status"],
                "filename": result["filename"],
                "locator": result["locator"],
                "source": result["source"],
                "score": result.get("score", 0),
                "project_ids": result.get("project_ids", []),
                "created_at": result["created_at"],
                "retrieval": {key: result[key] for key in ("score", "rank") if key in result},
            }
            search_results.append(search_result)

        query_time_ms = int((time.time() - start_time) * 1000)

        logger.info(
            f"搜索完成: 查询='{request.query}', 结果数={len(search_results)}, 耗时={query_time_ms}ms"
        )

        return SearchResponse(
            results=search_results, total=len(search_results), query_time_ms=query_time_ms
        )

    except Exception as e:
        logger.error(f"搜索失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"搜索失败: {str(e)}")
