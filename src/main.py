from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
import sys
from src.config.settings import settings
from src.api.router import api_router

# 配置日志
logger.remove()  # 移除默认 handler
logger.add(sys.stderr, level="INFO", format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{line} | {message}")
logger.add("logs/app_{time:YYYY-MM-DD}.log", rotation="100 MB", retention="30 days", level="DEBUG")

# 创建 FastAPI 应用
app = FastAPI(
    title="Research RAG Agent API",
    description="科研 RAG Agent 系统 API 接口",
    version="1.0.0"
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应配置具体的前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 API 路由
app.include_router(api_router, prefix="/api/v1")

# 健康检查端点
@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True
    )
