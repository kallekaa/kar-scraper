import pytest
from pydantic import ValidationError

from kar_scraper.config import Settings


def test_allowed_domains_accepts_empty_string() -> None:
    settings = Settings(
        OPENAI_API_KEY="test",
        FIRECRAWL_API_KEY="test",
        ALLOWED_DOMAINS="",
    )
    assert settings.allowed_domains == []


def test_allowed_domains_parses_comma_separated_values() -> None:
    settings = Settings(
        OPENAI_API_KEY="test",
        FIRECRAWL_API_KEY="test",
        ALLOWED_DOMAINS="Example.com, cdn.example.com",
    )
    assert settings.allowed_domains == ["example.com", "cdn.example.com"]


def test_allowed_domains_normalizes_urls_and_trailing_dots() -> None:
    settings = Settings(
        OPENAI_API_KEY="test",
        FIRECRAWL_API_KEY="test",
        ALLOWED_DOMAINS="https://Example.com/path, cdn.example.com.",
    )
    assert settings.allowed_domains == ["example.com", "cdn.example.com"]


def test_allowed_domains_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        Settings(
            OPENAI_API_KEY="test",
            FIRECRAWL_API_KEY="test",
            ALLOWED_DOMAINS="bad domain",
        )
