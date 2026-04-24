from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class AppSettings(BaseSettings):
    """Runtime settings for the file desk web app, ChatKit API, and MCP server."""

    openai_api_key: SecretStr = Field(init=False)
    clerk_secret_key: SecretStr = Field(init=False)
    app_signing_secret: SecretStr = Field(init=False)
    clerk_issuer_url: AnyHttpUrl = Field(init=False)

    app_base_url: AnyHttpUrl = "http://localhost:8000"
    clerk_domain: str | None = None
    clerk_client_id: str | None = None
    clerk_client_secret: SecretStr | None = None
    clerk_publishable_key: str | None = None
    clerk_active_metadata_key: str = "active"
    clerk_role_metadata_key: str = "role"
    clerk_clock_skew_ms: int = 5_000
    clerk_authorized_parties: Annotated[list[str], NoDecode] = Field(default_factory=list)
    database_url: str = "sqlite+aiosqlite:///./.local/openai-vectorstore-mcp-app.db"
    mcp_required_scopes: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["openid", "email", "profile"])
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    )
    static_dir: str = "ui/dist"
    chatkit_domain_key: str = "domain_pk_local_file_desk"

    openai_agent_model: str = "gpt-5.4-mini"
    openai_vision_model: str = "gpt-4.1-mini"
    openai_audio_transcription_model: str = "gpt-4o-transcribe-diarize"
    openai_poll_interval_ms: int = 1_000

    upload_session_max_age_seconds: int = 900
    asset_download_session_max_age_seconds: int = 900

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    app_name: str = "openai-file-desk"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("mcp_required_scopes", mode="before")
    @classmethod
    def _parse_required_scopes(cls, raw_value: object) -> list[str]:
        return cls._parse_string_list(raw_value, field_name="MCP_REQUIRED_SCOPES")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, raw_value: object) -> list[str]:
        return cls._parse_string_list(raw_value, field_name="CORS_ORIGINS")

    @staticmethod
    def _parse_string_list(raw_value: object, *, field_name: str) -> list[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, list):
            return [str(item).strip() for item in raw_value if str(item).strip()]
        if isinstance(raw_value, str):
            values = [part.strip() for part in raw_value.split(",")]
            return [value for value in values if value]
        raise TypeError(f"{field_name} must be a comma-separated string or list.")

    @property
    def normalized_app_base_url(self) -> str:
        return str(self.app_base_url).rstrip("/")

    @property
    def normalized_static_dir(self) -> str:
        return self.static_dir.strip().rstrip("/") or "ui/dist"

    @property
    def effective_clerk_domain(self) -> str:
        if isinstance(self.clerk_domain, str) and self.clerk_domain.strip():
            return self.clerk_domain.strip().removeprefix("https://").rstrip("/")
        parsed = urlparse(str(self.clerk_issuer_url))
        if not parsed.netloc:
            raise ValueError("Could not derive CLERK_DOMAIN from CLERK_ISSUER_URL.")
        return parsed.netloc

    @property
    def normalized_database_url(self) -> str:
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return self.database_url

    @property
    def sync_database_url(self) -> str:
        database_url = self.normalized_database_url
        if database_url.startswith("postgresql+asyncpg://"):
            return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
        if database_url.startswith("sqlite+aiosqlite://"):
            return database_url.replace("sqlite+aiosqlite://", "sqlite://", 1)
        return database_url


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Load and cache the app settings."""

    return AppSettings()
