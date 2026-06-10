from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import List

class Settings(BaseSettings):
    """應用程式設定"""

    # Database — SNOMED CT
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str

    # Database — ICD-10 PCS
    icd10_db: str = "icd10_pcs_db"

    # Database - Loinc
    loinc_db: str = "loinc_db"

    # LLM 後端
    llm_backend: str = "ollama"   # "ollama" | "vllm"
    llm_ip: str = "localhost"
    llm_port: int = 11434
    llm_model: str = "llama3"
    embedding_model: str = "all-minilm:latest"

    # vLLM（backend=vllm 時使用）
    vllm_base_url: str = ""
    vllm_model: str = ""
    vllm_temperature: float = 0.1
    vllm_connect_timeout: int = 5
    vllm_read_timeout: int = 180

    # RAG
    chunk_size: int = 800
    chunk_overlap: int = 80
    top_k: int = 4
    embedding_dimension: int = 384

    sentence_transformer_model: str = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"

    # RxNorm
    nlm_url: str = "https://rxnav.nlm.nih.gov/"

    # CORS
    cors_origins: List[str] = ["*"]

    # Swagger
    swagger_username: str = "admin"
    swagger_password: str = "admin"

    # LangSmith tracing
    langchain_tracing_v2: str = "false"
    langchain_api_key: str = ""
    langchain_project: str = "medical-coding-agent"

    # Computed properties
    @property
    def database_url(self) -> str:
        """SNOMED CT PostgreSQL 連線字串"""
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def icd10_database_url(self) -> str:
        """ICD-10 PCS PostgreSQL 連線字串"""
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.icd10_db}"

    @property
    def loinc_database_url(self) -> str:
        """LOINC PostgreSQL 連線字串"""
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.loinc_db}"

    @property
    def llm_base_url(self) -> str:
        """LLM API 位址（Ollama 用）"""
        return f"http://{self.llm_ip}:{self.llm_port}"

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"

@lru_cache()
def get_settings() -> Settings:
    """取得設定單例"""
    return Settings()
