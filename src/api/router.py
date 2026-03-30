from fastapi import APIRouter
from src.api import documents, chat, search, memories, projects, versions

api_router = APIRouter()

# 注册各模块路由
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(memories.router, prefix="/memories", tags=["memories"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(versions.router, prefix="/versions", tags=["versions"])
