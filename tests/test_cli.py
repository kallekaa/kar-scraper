from pathlib import Path

from typer.testing import CliRunner

from kar_scraper import cli
from kar_scraper.models import AgentResult, DownloadedFile, RankedCandidate, SearchIntent, WorkflowEvent


runner = CliRunner()


class FakeAgent:
    calls = []

    def __init__(self, settings, planner=None, searcher=None, reporter=None) -> None:
        self._reporter = reporter

    def run(self, options):
        self.__class__.calls.append(options)
        if self._reporter is not None:
            self._reporter(WorkflowEvent(stage="interpret", message="Interpreting request with LLM"))
            self._reporter(WorkflowEvent(stage="queries", message="Planned web searches", details={"queries": ["planned query"]}))
        downloaded = []
        if not options.dry_run:
            downloaded = [
                DownloadedFile(
                    url="https://example.com/song.kar",
                    filename="song-12345678.kar",
                    path=str(options.out_dir / "song-12345678.kar"),
                    sha256="1234567890abcdef",
                    size_bytes=10,
                    reason="matches request",
                )
            ]
        return AgentResult(
            request=options.request,
            intent=SearchIntent(
                search_intent=options.request,
                max_results=options.limit,
                search_queries=["planned query"],
            ),
            queries=["planned query"],
            candidates=[
                RankedCandidate(
                    url="https://example.com/song.kar",
                    accepted=True,
                    score=0.9,
                    reason="matches request",
                )
            ],
            downloaded=downloaded,
        )


def setup_fake_cli(monkeypatch) -> None:
    FakeAgent.calls = []
    monkeypatch.setattr(cli, "KarAgent", FakeAgent)
    monkeypatch.setattr(cli, "get_settings", lambda: object())


def test_cli_runs_one_shot_request(monkeypatch, tmp_path: Path) -> None:
    setup_fake_cli(monkeypatch)
    result = runner.invoke(cli.app, ["Queen Test Song", "--dry-run", "--out", str(tmp_path)])
    assert result.exit_code == 0
    assert len(FakeAgent.calls) == 1
    assert FakeAgent.calls[0].request == "Queen Test Song"
    assert "Ranked .kar Candidates" in result.output


def test_cli_prompts_when_request_is_omitted(monkeypatch, tmp_path: Path) -> None:
    setup_fake_cli(monkeypatch)
    result = runner.invoke(cli.app, ["--dry-run", "--out", str(tmp_path)], input="Queen Test Song\nn\n")
    assert result.exit_code == 0
    assert len(FakeAgent.calls) == 1
    assert FakeAgent.calls[0].request == "Queen Test Song"
    assert "Do a new request?" in result.output


def test_cli_interactive_loop_accepts_multiple_requests(monkeypatch, tmp_path: Path) -> None:
    setup_fake_cli(monkeypatch)
    result = runner.invoke(
        cli.app,
        ["--dry-run", "--out", str(tmp_path)],
        input="Queen Test Song\ny\nABBA Dancing Queen\nn\n",
    )
    assert result.exit_code == 0
    assert [call.request for call in FakeAgent.calls] == ["Queen Test Song", "ABBA Dancing Queen"]


def test_cli_quiet_suppresses_workflow_progress(monkeypatch, tmp_path: Path) -> None:
    setup_fake_cli(monkeypatch)
    result = runner.invoke(cli.app, ["Queen Test Song", "--dry-run", "--quiet", "--out", str(tmp_path)])
    assert result.exit_code == 0
    assert "Interpreting request with LLM" not in result.output
    assert "Ranked .kar Candidates" in result.output
