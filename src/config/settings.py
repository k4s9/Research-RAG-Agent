from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    postgres_url: str = "postgresql+asyncpg://raguser:changeme@localhost:5432/rag_db"
    milvus_uri: str = "http://localhost:19530"

    # Models
    bge_m3_model_path: str = "BAAI/bge-m3"
    reranker_model_path: str = "BAAI/bge-reranker-v2-m3"

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

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    gradio_port: int = 7860

settings = Settings()
