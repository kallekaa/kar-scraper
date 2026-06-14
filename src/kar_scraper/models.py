from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from kar_scraper.config import normalize_allowed_domain


class SearchIntent(BaseModel):
    artist: str | None = None
    song: str | None = None
    genre: str | None = None
    era: str | None = None
    max_results: int = Field(default=10, ge=1, le=50)
    search_intent: str
    search_queries: list[str] = Field(default_factory=list)


class SearchResult(BaseModel):
    title: str | None = None
    url: str
    description: str | None = None


class PageContent(BaseModel):
    source_url: str
    markdown: str = ""
    html: str = ""
    title: str | None = None


class KarCandidate(BaseModel):
    url: str
    source_page: str | None = None
    title: str | None = None
    reason: str = "Direct .kar link found on a public page."
    score: float = Field(default=0.0, ge=0.0, le=1.0)


class RankedCandidate(KarCandidate):
    accepted: bool = True


class DownloadedFile(BaseModel):
    url: str
    source_page: str | None = None
    filename: str
    path: str
    sha256: str
    size_bytes: int
    reason: str
    downloaded_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class AgentResult(BaseModel):
    request: str
    intent: SearchIntent
    queries: list[str]
    candidates: list[RankedCandidate]
    downloaded: list[DownloadedFile] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class CliOptions(BaseModel):
    request: str
    out_dir: Path
    limit: int = Field(ge=1, le=50)
    dry_run: bool = False
    allowed_domains: list[str] = Field(default_factory=list)

    @field_validator("allowed_domains", mode="before")
    @classmethod
    def normalize_allowed_domains(cls, value: object) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, list):
            values = value
        else:
            raise TypeError("allowed_domains must be a string or list")
        return [normalize_allowed_domain(str(item)) for item in values if str(item).strip()]


class WorkflowEvent(BaseModel):
    stage: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class Manifest(BaseModel):
    request: str
    generated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    files: list[DownloadedFile]
    errors: list[str] = Field(default_factory=list)


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("$defs", None)
    return schema
