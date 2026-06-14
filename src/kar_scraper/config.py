from __future__ import annotations

from functools import lru_cache
import re
from typing import Annotated
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def normalize_allowed_domain(value: str) -> str:
    raw = value.strip().lower()
    if not raw:
        raise ValueError("Allowed domain cannot be empty")

    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    host = parsed.hostname or ""
    normalized = host.rstrip(".")
    if not normalized or not _valid_domain_or_ipv4(normalized):
        raise ValueError(f"Invalid allowed domain: {value!r}")
    return normalized


def _valid_domain_or_ipv4(value: str) -> bool:
    if IPV4_RE.match(value):
        return all(0 <= int(part) <= 255 for part in value.split("."))
    return bool(DOMAIN_RE.match(value))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    firecrawl_api_key: str = Field(default="", alias="FIRECRAWL_API_KEY")
    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")
    max_downloads: int = Field(default=10, ge=1, le=50, alias="MAX_DOWNLOADS")
    request_timeout_seconds: float = Field(default=30.0, gt=0, alias="REQUEST_TIMEOUT_SECONDS")
    allowed_domains: Annotated[list[str], NoDecode] = Field(default_factory=list, alias="ALLOWED_DOMAINS")
    max_file_size_bytes: int = 2_000_000

    @field_validator("allowed_domains", mode="before")
    @classmethod
    def parse_allowed_domains(cls, value: object) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [normalize_allowed_domain(part) for part in value.split(",") if part.strip()]
        if isinstance(value, list):
            return [normalize_allowed_domain(str(part)) for part in value if str(part).strip()]
        raise TypeError("ALLOWED_DOMAINS must be a comma-separated string or list")

    def require_openai_key(self) -> None:
        if not self.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required. Copy .env.example to .env and set it.")

    def require_firecrawl_key(self) -> None:
        if not self.firecrawl_api_key:
            raise RuntimeError("FIRECRAWL_API_KEY is required. Copy .env.example to .env and set it.")


@lru_cache
def get_settings() -> Settings:
    return Settings()
