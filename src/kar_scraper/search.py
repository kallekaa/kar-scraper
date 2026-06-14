from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

from firecrawl import V1FirecrawlApp
from tenacity import retry, stop_after_attempt, wait_exponential

from kar_scraper.config import Settings
from kar_scraper.models import KarCandidate, PageContent, SearchResult


class _HrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name.lower() in {"href", "src"} and value:
                self.links.append(value)


URL_RE = re.compile(r"https?://[^\s<>'\")]+", re.IGNORECASE)
MD_LINK_RE = re.compile(r"\[[^\]]*]\(([^)]+)\)")


class FirecrawlSearch:
    def __init__(self, settings: Settings) -> None:
        settings.require_firecrawl_key()
        self._client = V1FirecrawlApp(api_key=settings.firecrawl_api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def search(self, query: str, limit: int) -> list[SearchResult]:
        result = self._client.search(query, limit=limit)
        rows = _extract_rows(result, keys=("web", "data", "results"))
        return [_search_result_from_row(row) for row in rows if _search_result_from_row(row)]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def scrape(self, url: str) -> PageContent:
        result = self._client.scrape_url(url, formats=["markdown", "html"])
        return _page_content_from_result(url, result)


def build_queries(
    intent_text: str,
    artist: str | None,
    song: str | None,
    genre: str | None,
    era: str | None,
    planned_queries: list[str] | None = None,
) -> list[str]:
    parts = [part for part in [artist, song, genre, era] if part]
    focused = " ".join(parts) if parts else intent_text
    fallback_queries = [
        f"{focused} filetype:kar",
        f"{focused} karaoke .kar",
        f"{focused} download .kar",
    ]
    if artist and song:
        fallback_queries.insert(0, f'"{artist}" "{song}" filetype:kar')
    return _dedupe_preserve_order([*(planned_queries or []), *fallback_queries])


def extract_kar_candidates(page: PageContent, allowed_domains: list[str] | None = None) -> list[KarCandidate]:
    allowed = [domain.lower() for domain in allowed_domains or []]
    raw_links: list[str] = []

    parser = _HrefParser()
    parser.feed(page.html or "")
    raw_links.extend(parser.links)
    raw_links.extend(URL_RE.findall(page.html or ""))
    raw_links.extend(URL_RE.findall(page.markdown or ""))
    raw_links.extend(match.group(1) for match in MD_LINK_RE.finditer(page.markdown or ""))

    candidates: list[KarCandidate] = []
    seen: set[str] = set()
    for raw_link in raw_links:
        url = normalize_candidate_url(urljoin(page.source_url, raw_link.strip().strip("'\"")))
        if not is_direct_kar_url(url):
            continue
        if allowed and not _domain_allowed(url, allowed):
            continue
        if url in seen:
            continue
        seen.add(url)
        candidates.append(
            KarCandidate(
                url=url,
                source_page=page.source_url,
                title=page.title,
            )
        )
    return candidates


def is_direct_kar_url(url: str) -> bool:
    parsed = urlparse(url)
    return bool(parsed.hostname) and parsed.scheme in {"http", "https"} and parsed.path.lower().endswith(".kar")


def normalize_candidate_url(url: str) -> str:
    return urldefrag(url.strip())[0]


def _domain_allowed(url: str, allowed_domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = " ".join(value.split())
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


def _extract_rows(result: Any, keys: tuple[str, ...]) -> list[Any]:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in keys:
            value = result.get(key)
            if isinstance(value, list):
                return value
        return []
    for key in keys:
        value = getattr(result, key, None)
        if isinstance(value, list):
            return value
    return []


def _get_value(row: Any, *names: str) -> Any:
    if isinstance(row, dict):
        for name in names:
            if name in row:
                return row[name]
    for name in names:
        value = getattr(row, name, None)
        if value is not None:
            return value
    return None


def _search_result_from_row(row: Any) -> SearchResult | None:
    url = _get_value(row, "url", "link")
    if not url:
        return None
    return SearchResult(
        url=str(url),
        title=_get_value(row, "title"),
        description=_get_value(row, "description", "snippet"),
    )


def _page_content_from_result(url: str, result: Any) -> PageContent:
    metadata = _get_value(result, "metadata") or {}
    source_url = _get_value(metadata, "source_url", "sourceURL") or url
    title = _get_value(metadata, "title", "og_title", "ogTitle")
    return PageContent(
        source_url=str(source_url),
        markdown=str(_get_value(result, "markdown") or ""),
        html=str(_get_value(result, "html") or ""),
        title=str(title) if title else None,
    )
