from fastapi import APIRouter, HTTPException
from src.schemas.version import VersionHistoryResponse

router = APIRouter()

@router.get("/{entity_id}/history", response_model=VersionHistoryResponse)
async def get_version_history(entity_id: str):
    """获取知识版本历史"""
    # 这里将实现版本历史查询逻辑
    # 暂时返回模拟响应
    return VersionHistoryResponse(
        entity_id=entity_id,
        entity_type="memory",
        current_status="active",
        history=[
            {
                "version": 3,
                "action": "create",
                "summary": "改用 Flash Attention 方案",
                "reason": "导师指示更换",
                "created_at": "2026-03-28T10:00:00Z"
            },
            {
                "version": 2,
                "action": "outdated",
                "summary": "使用标准 Multi-Head Attention",
                "reason": "导师认为效率太低，被 v3 取代",
                "created_at": "2026-03-28T10:00:00Z"
            },
            {
                "version": 1,
                "action": "create",
                "summary": "使用标准 Multi-Head Attention",
                "reason": None,
                "created_at": "2026-03-15T09:00:00Z"
            }
        ]
    )
