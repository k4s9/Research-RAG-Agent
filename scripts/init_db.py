import asyncio
from sqlalchemy.ext.asyncio import AsyncEngine
from src.db.postgres import engine
from src.db.models import Base

async def init_db():
    """初始化数据库表结构"""
    async with engine.begin() as conn:
        # 先删除所有表（如果存在）
        await conn.run_sync(Base.metadata.drop_all)
        # 创建所有表
        await conn.run_sync(Base.metadata.create_all)
    print("数据库表结构初始化完成")

if __name__ == "__main__":
    asyncio.run(init_db())
