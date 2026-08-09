from setuptools import setup, find_packages

setup(
    name="research-rag-agent",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "fastapi>=0.110.0",
        "gradio>=4.26.0",
        "sqlalchemy>=2.0.29",
        "asyncpg>=0.29.0",
        "pymilvus>=2.4.3",
        "flagembedding>=1.2.16",
        "pymupdf>=1.24.5",
        "python-pptx>=0.6.23",
        "paddleocr>=2.7.0.3",
        "pydantic>=2.6.1",
        "pydantic-settings>=2.1.0",
        "loguru>=0.7.2",
        "aiofiles>=23.2.1",
        "python-dotenv>=1.0.1",
        "uvicorn>=0.28.0",
        "alembic>=1.13.0",
    ],
    extras_require={"dev": ["pytest", "pytest-asyncio", "ruff", "pre-commit"]},
    python_requires=">=3.10",
)
