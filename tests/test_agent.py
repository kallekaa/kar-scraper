from pathlib import Path

from kar_scraper.agent import KarAgent
from kar_scraper.config import Settings
from kar_scraper.models import CliOptions, PageContent, RankedCandidate, SearchIntent, SearchResult, WorkflowEvent


class FakePlanner:
    def interpret_request(self, request: str, max_results: int) -> SearchIntent:
        return SearchIntent(
            artist="Queen",
            song="Test Song",
            max_results=max_results,
            search_intent=request,
            search_queries=["llm planned query filetype:kar"],
        )

    def rank_candidates(self, request, intent, candidates, limit):
        return [
            RankedCandidate(**(candidate.model_dump() | {"accepted": True, "score": 0.9, "reason": "matches request"}))
            for candidate in candidates[:limit]
        ]


class FakeSearcher:
    def search(self, query: str, limit: int):
        return [SearchResult(url="https://example.com/page.html", title="Page")]

    def scrape(self, url: str) -> PageContent:
        return PageContent(
            source_url=url,
            html='<a href="https://example.com/files/test-song.kar">download</a>',
        )


class SearcherWithOneFailure(FakeSearcher):
    def search(self, query: str, limit: int):
        if query == "llm planned query filetype:kar":
            raise RuntimeError("provider unavailable")
        return super().search(query, limit)


def test_agent_dry_run_returns_ranked_candidates(tmp_path: Path) -> None:
    settings = Settings(
        OPENAI_API_KEY="test",
        FIRECRAWL_API_KEY="test",
        MAX_DOWNLOADS=5,
    )
    agent = KarAgent(settings, planner=FakePlanner(), searcher=FakeSearcher())
    result = agent.run(CliOptions(request="Queen Test Song", out_dir=tmp_path, limit=3, dry_run=True))
    assert result.candidates[0].url == "https://example.com/files/test-song.kar"
    assert result.queries[0] == "llm planned query filetype:kar"
    assert result.downloaded == []
    assert not (tmp_path / "manifest.json").exists()


def test_agent_reports_workflow_events(tmp_path: Path) -> None:
    settings = Settings(
        OPENAI_API_KEY="test",
        FIRECRAWL_API_KEY="test",
        MAX_DOWNLOADS=5,
    )
    events: list[WorkflowEvent] = []
    agent = KarAgent(settings, planner=FakePlanner(), searcher=FakeSearcher(), reporter=events.append)
    agent.run(CliOptions(request="Queen Test Song", out_dir=tmp_path, limit=3, dry_run=True))
    stages = [event.stage for event in events]
    assert stages[:3] == ["request", "interpret", "interpret"]
    assert "queries" in stages
    assert "search" in stages
    assert "rank" in stages


def test_agent_continues_after_search_failure(tmp_path: Path) -> None:
    settings = Settings(
        OPENAI_API_KEY="test",
        FIRECRAWL_API_KEY="test",
        MAX_DOWNLOADS=5,
    )
    agent = KarAgent(settings, planner=FakePlanner(), searcher=SearcherWithOneFailure())
    result = agent.run(CliOptions(request="Queen Test Song", out_dir=tmp_path, limit=3, dry_run=True))
    assert result.candidates[0].url == "https://example.com/files/test-song.kar"
    assert "Search failed for 'llm planned query filetype:kar': provider unavailable" in result.errors
