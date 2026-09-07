from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.config.settings import settings

# 创建异步引擎
engine_options = {"echo": False}
if not settings.postgres_url.startswith("sqlite+"):
    engine_options.update(pool_size=10, max_overflow=20)
engine = create_async_engine(settings.postgres_url, **engine_options)

# 创建异步会话工厂
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# 依赖项：获取数据库会话
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
