"""Central configuration loaded from environment variables."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    assembly_api_key: str = Field(default="", alias="ASSEMBLY_API_KEY")
    law_api_key: str = Field(default="", alias="LAW_API_KEY")
    law_api_email: str = Field(default="", alias="LAW_API_EMAIL")
    voyage_api_key: str = Field(default="", alias="VOYAGE_API_KEY")
    cohere_api_key: str = Field(default="", alias="COHERE_API_KEY")

    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")
    qdrant_api_key: str = Field(default="", alias="QDRANT_API_KEY")
    qdrant_collection: str = Field(default="legi_documents")

    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    legi_env: Literal["development", "staging", "production"] = Field(
        default="development", alias="LEGI_ENV"
    )
    legi_log_level: str = Field(default="INFO", alias="LEGI_LOG_LEVEL")
    legi_data_dir: Path = Field(default=Path("./data"), alias="LEGI_DATA_DIR")

    claude_model_primary: str = "claude-opus-4-7"
    claude_model_fast: str = "claude-haiku-4-5-20251001"
    embedding_model: str = "voyage-3"
    embedding_dim: int = 1024

    @property
    def raw_dir(self) -> Path:
        p = self.legi_data_dir / "raw"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def processed_dir(self) -> Path:
        p = self.legi_data_dir / "processed"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def logs_dir(self) -> Path:
        p = self.legi_data_dir / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def eval_dir(self) -> Path:
        p = self.legi_data_dir / "eval"
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
