from fastapi import APIRouter, HTTPException
from src.schemas.project import ProjectCreateRequest, ProjectResponse, ProjectListResponse

router = APIRouter()

@router.post("", response_model=ProjectResponse)
async def create_project(request: ProjectCreateRequest):
    """创建项目"""
    # 这里将实现项目创建逻辑
    # 暂时返回模拟响应
    return ProjectResponse(
        id="proj-uuid-xxx",
        name=request.name,
        description=request.description,
        created_at="2026-03-30T10:00:00Z",
        updated_at="2026-03-30T10:00:00Z"
    )

@router.get("", response_model=ProjectListResponse)
async def get_projects():
    """获取项目列表"""
    # 这里将实现项目列表查询逻辑
    # 暂时返回模拟响应
    return ProjectListResponse(
        projects=[
            {
                "id": "proj-uuid-xxx",
                "name": "注意力机制研究",
                "description": "研究 Transformer 注意力机制的优化方案",
                "created_at": "2026-03-30T10:00:00Z",
                "updated_at": "2026-03-30T10:00:00Z"
            }
        ],
        total=1
    )
