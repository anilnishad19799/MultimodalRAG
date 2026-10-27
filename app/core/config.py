from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    session_secret: str = "development-only-change-this-session-secret"
    session_cookie_secure: bool = False
    log_level: str = "INFO"
    model_provider: Literal["openai", "groq", "gemini"] = "openai"
    openai_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    groq_model: str = "qwen/qwen3.8-27b"
    gemini_model: str = "gemini-3.7-flash"
    embedding_model: str = "text-embedding-3-large"
    embedding_dimension: int = Field(default=3072, gt=0)
    database_url: str = "sqlite:///./rag.db"
    celery_broker_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "multimodal_rag_openai_large"
    s3_endpoint: str = "localhost:9000"
    s3_access_key: str = ""
    s3_secret_key: SecretStr = SecretStr("")
    s3_bucket: str = "rag-assets"
    s3_secure: bool = False
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    max_image_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    max_pdf_pages: int = Field(default=300, gt=0)
    pdf_render_dpi: int = Field(default=144, ge=72, le=300)
    retrieval_limit: int = Field(default=8, ge=1, le=50)

    @model_validator(mode="after")
    def validate_production_auth(self):
        if self.app_env == "production":
            if len(self.session_secret) < 32 or self.session_secret.startswith("development-only"):
                raise ValueError("Set SESSION_SECRET to a random value of at least 32 characters in production")
            if not self.session_cookie_secure:
                raise ValueError("SESSION_COOKIE_SECURE must be enabled in production HTTPS deployments")
        return self

    @property
    def selected_api_key(self) -> str:
        secret = {
            "openai": self.openai_api_key,
            "groq": self.groq_api_key,
            "gemini": self.gemini_api_key,
        }[self.model_provider]
        value = secret.get_secret_value().strip() if secret else ""
        if not value:
            raise ValueError(f"{self.model_provider.upper()}_API_KEY must be configured")
        return value

    @property
    def selected_model(self) -> str:
        return {
            "openai": self.openai_model,
            "groq": self.groq_model,
            "gemini": self.gemini_model,
        }[self.model_provider]

    @property
    def embedding_api_key(self) -> str:
        value = self.openai_api_key.get_secret_value().strip() if self.openai_api_key else ""
        if not value:
            raise ValueError("OPENAI_API_KEY must be configured for OpenAI embeddings")
        return value

    @property
    def max_user_images(self) -> int:
        return 2 if self.model_provider == "groq" else 3

    @property
    def max_total_images(self) -> int:
        return 3 if self.model_provider == "groq" else 8


@lru_cache
def get_settings() -> Settings:
    return Settings()
