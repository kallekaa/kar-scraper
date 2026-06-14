from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from kar_scraper.config import Settings, normalize_allowed_domain
from kar_scraper.download import DownloadError, download_candidate, existing_manifest_entries, write_manifest
from kar_scraper.llm import OpenAIPlanner
from kar_scraper.models import (
    AgentResult,
    CliOptions,
    DownloadedFile,
    KarCandidate,
    PageContent,
    RankedCandidate,
    SearchIntent,
    WorkflowEvent,
)
from kar_scraper.search import FirecrawlSearch, build_queries, extract_kar_candidates


Reporter = Callable[[WorkflowEvent], None]


class Planner(Protocol):
    def interpret_request(self, request: str, max_results: int) -> SearchIntent:
        ...

    def rank_candidates(
        self,
        request: str,
        intent: SearchIntent,
        candidates: list[KarCandidate],
        limit: int,
    ) -> list[RankedCandidate]:
        ...


class Searcher(Protocol):
    def search(self, query: str, limit: int) -> list:
        ...

    def scrape(self, url: str) -> PageContent:
        ...


class KarAgentState(TypedDict, total=False):
    request: str
    out_dir: Path
    limit: int
    dry_run: bool
    allowed_domains: list[str]
    intent: SearchIntent
    queries: list[str]
    search_urls: list[str]
    pages: list[PageContent]
    candidates: list[KarCandidate]
    ranked_candidates: list[RankedCandidate]
    downloaded: list[DownloadedFile]
    errors: list[str]


class KarAgent:
    def __init__(
        self,
        settings: Settings,
        planner: Planner | None = None,
        searcher: Searcher | None = None,
        reporter: Reporter | None = None,
    ) -> None:
        self._settings = settings
        self._planner = planner or OpenAIPlanner(settings)
        self._searcher = searcher or FirecrawlSearch(settings)
        self._reporter = reporter
        self._graph = self._build_graph()

    def run(self, options: CliOptions) -> AgentResult:
        effective_limit = min(options.limit, self._settings.max_downloads)
        self._emit(
            "request",
            "Received request",
            request=options.request,
            limit=effective_limit,
            dry_run=options.dry_run,
        )
        state = self._graph.invoke(
            {
                "request": options.request,
                "out_dir": options.out_dir,
                "limit": effective_limit,
                "dry_run": options.dry_run,
                "allowed_domains": options.allowed_domains,
                "errors": [],
            }
        )
        return AgentResult(
            request=state["request"],
            intent=state["intent"],
            queries=state.get("queries", []),
            candidates=state.get("ranked_candidates", []),
            downloaded=state.get("downloaded", []),
            errors=state.get("errors", []),
        )

    def _emit(self, stage: str, message: str, **details: object) -> None:
        if self._reporter is not None:
            self._reporter(WorkflowEvent(stage=stage, message=message, details=dict(details)))

    def _build_graph(self):
        graph = StateGraph(KarAgentState)
        graph.add_node("interpret_request", self._interpret_request)
        graph.add_node("build_queries", self._build_queries)
        graph.add_node("search_web", self._search_web)
        graph.add_node("scrape_pages", self._scrape_pages)
        graph.add_node("extract_links", self._extract_links)
        graph.add_node("rank_candidates", self._rank_candidates)
        graph.add_node("download_verify", self._download_verify)
        graph.add_node("save_manifest", self._save_manifest)

        graph.add_edge(START, "interpret_request")
        graph.add_edge("interpret_request", "build_queries")
        graph.add_edge("build_queries", "search_web")
        graph.add_edge("search_web", "scrape_pages")
        graph.add_edge("scrape_pages", "extract_links")
        graph.add_edge("extract_links", "rank_candidates")
        graph.add_conditional_edges(
            "rank_candidates",
            lambda state: "finish" if state.get("dry_run") else "download",
            {"finish": END, "download": "download_verify"},
        )
        graph.add_edge("download_verify", "save_manifest")
        graph.add_edge("save_manifest", END)
        return graph.compile()

    def _interpret_request(self, state: KarAgentState) -> KarAgentState:
        self._emit("interpret", "Interpreting request with LLM")
        intent = self._planner.interpret_request(state["request"], state["limit"])
        self._emit(
            "interpret",
            "LLM interpreted intent",
            search_intent=intent.search_intent,
            artist=intent.artist,
            song=intent.song,
            genre=intent.genre,
            era=intent.era,
        )
        return {"intent": intent}

    def _build_queries(self, state: KarAgentState) -> KarAgentState:
        intent = state["intent"]
        result = {
            "queries": build_queries(
                intent.search_intent,
                intent.artist,
                intent.song,
                intent.genre,
                intent.era,
                intent.search_queries,
            )
        }
        self._emit("queries", "Planned web searches", queries=result["queries"])
        return result

    def _search_web(self, state: KarAgentState) -> KarAgentState:
        urls: list[str] = []
        errors = list(state.get("errors", []))
        seen: set[str] = set()
        per_query_limit = max(3, min(state["limit"], 10))
        for query in state.get("queries", []):
            try:
                self._emit("search", "Searching web", query=query, limit=per_query_limit)
                before_count = len(urls)
                for result in self._searcher.search(query, per_query_limit):
                    url = str(getattr(result, "url", ""))
                    if url and url not in seen:
                        seen.add(url)
                        urls.append(url)
                self._emit("search", "Search finished", query=query, new_urls=len(urls) - before_count)
            except Exception as exc:  # provider errors should not stop other queries
                errors.append(f"Search failed for {query!r}: {exc}")
                self._emit("search", "Search failed", query=query, error=str(exc))
        self._emit("search", "Collected search result pages", count=min(len(urls), 30))
        return {"search_urls": urls[:30], "errors": errors}

    def _scrape_pages(self, state: KarAgentState) -> KarAgentState:
        pages: list[PageContent] = []
        errors = list(state.get("errors", []))
        for url in state.get("search_urls", []):
            try:
                self._emit("scrape", "Scraping page", url=url)
                pages.append(self._searcher.scrape(url))
            except Exception as exc:
                errors.append(f"Scrape failed for {url}: {exc}")
                self._emit("scrape", "Scrape failed", url=url, error=str(exc))
        self._emit("scrape", "Scraped pages", count=len(pages))
        return {"pages": pages, "errors": errors}

    def _extract_links(self, state: KarAgentState) -> KarAgentState:
        allowed = _combined_allowed_domains(self._settings.allowed_domains, state.get("allowed_domains", []))
        candidates: list[KarCandidate] = []
        seen: set[str] = set()
        for page in state.get("pages", []):
            for candidate in extract_kar_candidates(page, allowed):
                if candidate.url not in seen:
                    seen.add(candidate.url)
                    candidates.append(candidate)
        self._emit("extract", "Extracted direct .kar candidates", count=len(candidates))
        return {"candidates": candidates}

    def _rank_candidates(self, state: KarAgentState) -> KarAgentState:
        self._emit("rank", "Ranking candidates with LLM", count=len(state.get("candidates", [])))
        ranked = self._planner.rank_candidates(
            state["request"],
            state["intent"],
            state.get("candidates", []),
            state["limit"],
        )
        self._emit("rank", "LLM accepted candidates", count=len(ranked))
        return {"ranked_candidates": ranked}

    def _download_verify(self, state: KarAgentState) -> KarAgentState:
        out_dir = state["out_dir"]
        manifest_path = out_dir / "manifest.json"
        downloaded: list[DownloadedFile] = []
        errors = list(state.get("errors", []))
        known_files = existing_manifest_entries(manifest_path, errors)

        for candidate in state.get("ranked_candidates", []):
            if len(downloaded) >= state["limit"]:
                break
            try:
                self._emit("download", "Downloading candidate", url=candidate.url)
                item = download_candidate(candidate, state["intent"], out_dir, self._settings, known_files + downloaded)
                downloaded.append(item)
                self._emit("download", "Saved file", filename=item.filename, size_bytes=item.size_bytes)
            except (DownloadError, OSError, Exception) as exc:
                errors.append(f"Skipped {candidate.url}: {exc}")
                self._emit("download", "Skipped candidate", url=candidate.url, error=str(exc))
        self._emit("download", "Download step finished", count=len(downloaded))
        return {"downloaded": downloaded, "errors": errors}

    def _save_manifest(self, state: KarAgentState) -> KarAgentState:
        write_manifest(
            state["out_dir"] / "manifest.json",
            state["request"],
            state.get("downloaded", []),
            state.get("errors", []),
        )
        self._emit("manifest", "Saved manifest", path=str(state["out_dir"] / "manifest.json"))
        return {}


def _combined_allowed_domains(settings_domains: list[str], cli_domains: list[str]) -> list[str]:
    combined = []
    seen: set[str] = set()
    for domain in [*settings_domains, *cli_domains]:
        normalized = normalize_allowed_domain(domain)
        if normalized and normalized not in seen:
            seen.add(normalized)
            combined.append(normalized)
    return combined
