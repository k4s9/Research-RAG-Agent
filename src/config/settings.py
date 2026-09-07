from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    postgres_url: str = "postgresql+asyncpg://raguser:changeme@localhost:5432/rag_db"
    milvus_uri: str = "http://localhost:19530"
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
    llm_model_name: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com"

    # Retrieval
    dense_top_k: int = 50
    sparse_top_k: int = 50
    rrf_k: int = 60
    rerank_top_n: int = 20
    final_top_k: int = 5
    time_decay_alpha: float = 0.01
    time_decay_lambda: float = 0.3

    # OCR
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
