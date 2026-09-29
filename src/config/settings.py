from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    # Database
    postgres_url: str = Field(
        default="postgresql+asyncpg://raguser:changeme@localhost:5432/rag_db",
        validation_alias=AliasChoices("POSTGRES_URL", "DATABASE_URL"),
    )

    @field_validator("postgres_url")
    @classmethod
    def async_dsn(cls, value: str) -> str:
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)

    milvus_uri: str = "http://localhost:19530"
    milvus_token: str = ""
    milvus_collection: str = "knowledge_chunks"
    vector_store_backend: str = "milvus"

    # Models
    bge_m3_model_path: str = "BAAI/bge-m3"
    reranker_model_path: str = "BAAI/bge-reranker-v2-m3"
    embedding_provider: str = "remote"
    embedding_base_url: str = "http://localhost:8000"
    embedding_endpoint: str = "/v1/embeddings"
    embedding_api_key: str = ""
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_dimension: int = 1024
    embedding_timeout_seconds: float = 30.0
    reranker_provider: str = "remote"
    reranker_model: str = "Qwen/Qwen3-Reranker-0.6B"
    reranker_base_url: str = "http://localhost:8001"
    reranker_endpoint: str = "/rerank"
    reranker_api_key: str = ""
    reranker_timeout_seconds: float = 30.0
    request_retry_attempts: int = 2
    request_retry_backoff_seconds: float = 0.25

    # LLM
    llm_provider: str = "deepseek"  # or "openai"
    llm_api_key: str = ""
    llm_model_name: str = Field(
        default="deepseek-chat", validation_alias=AliasChoices("LLM_MODEL_NAME", "LLM_MODEL")
    )
    llm_base_url: str = "https://api.deepseek.com"
    llm_timeout_seconds: float = Field(default=180, gt=0, le=900)
    llm_reasoning_effort: Literal["low", "medium", "high"] | None = None
    document_enrichment_timeout_seconds: float = 180.0

    @field_validator("llm_reasoning_effort", mode="before")
    @classmethod
    def optional_reasoning_effort(cls, value):
        return None if value == "" else value

    # Retrieval
    dense_top_k: int = 50
    sparse_top_k: int = 50
    rrf_k: int = 60
    rerank_top_n: int = 20
    final_top_k: int = 5
    time_decay_alpha: float = 0.01
    time_decay_lambda: float = 0.3

    # Agent loop
    agent_max_steps: int = 12
    agent_max_model_calls: int = 6
    agent_max_tool_calls: int = 8
    agent_search_top_k: int = Field(default=3, ge=1, le=20)
    agent_max_active_seconds: float = 600
    agent_max_total_tokens: int = 128000
    agent_max_context_tokens: int = 65536
    agent_max_output_tokens: int = 2048
    agent_max_result_chars: int = 30000
    agent_max_total_result_chars: int = 120000
    llm_input_price_per_million: float | None = None
    llm_output_price_per_million: float | None = None
    context_recent_turns: int = 6

    # OCR
    # Optional local tokenizer.json matching the deployed embedding model.
    # Unset means explicitly measured UTF-8 bytes, not model token counts.
    pdf_tokenizer_path: str | None = None

    @field_validator("pdf_tokenizer_path", mode="before")
    @classmethod
    def optional_pdf_tokenizer(cls, value):
        return None if value == "" else value

    use_ocr: bool = True
    ocr_lang: str = "ch"

    # Paths
    upload_dir: str = "./data/uploads"
    image_dir: str = "./data/images"
    max_upload_size_bytes: int = 50 * 1024 * 1024

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8002
    api_client_url: str = "http://127.0.0.1:8002"
    gradio_port: int = 7860


settings = Settings()
