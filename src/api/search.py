from fastapi import APIRouter, HTTPException
from src.schemas.search import SearchRequest, SearchResponse

router = APIRouter()

@router.post("", response_model=SearchResponse)
async def search(request: SearchRequest):
    """执行搜索"""
    # 这里将实现搜索逻辑
    # 暂时返回模拟响应
    return SearchResponse(
        results=[
            {
                "id": "chunk-uuid-xxx",
                "entity_type": "memory",
                "content": "导师认为当前 Transformer 架构的计算开销过大...",
                "content_type": "decision",
                "version_status": "active",
                "source": "conversation @ 2026-03-25",
                "score": 0.89,
                "project_ids": ["proj-uuid-1"],
                "created_at": "2026-03-25T14:30:00Z"
            }
        ],
        total=5,
        query_time_ms=245
    )
