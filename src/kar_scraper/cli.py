from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich import box
from rich.console import Console
from rich.table import Table

from kar_scraper.agent import KarAgent
from kar_scraper.config import get_settings
from kar_scraper.models import CliOptions, WorkflowEvent

app = typer.Typer(help="Find and download public direct .kar karaoke files.")
console = Console()


@app.command()
def main(
    request: Annotated[str | None, typer.Argument(help="Artist, song, genre, or free-text karaoke request.")] = None,
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")] = Path("downloads"),
    limit: Annotated[int, typer.Option("--limit", "-l", min=1, max=50, help="Maximum accepted candidates/downloads.")] = 10,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show candidates without downloading.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="Suppress verbose workflow progress.")] = False,
    allowed_domain: Annotated[
        list[str] | None,
        typer.Option("--allowed-domain", help="Restrict direct .kar links to this domain. Can be repeated."),
    ] = None,
) -> None:
    settings = get_settings()
    if request:
        exit_code = _run_once(request, settings, out, limit, dry_run, quiet, allowed_domain or [])
        if exit_code:
            raise typer.Exit(code=exit_code)
        return

    while True:
        next_request = typer.prompt("Request", default="", show_default=False).strip()
        if not next_request:
            return
        exit_code = _run_once(next_request, settings, out, limit, dry_run, quiet, allowed_domain or [])
        if exit_code == 2:
            raise typer.Exit(code=exit_code)
        if not typer.confirm("Do a new request?", default=True):
            return


def _run_once(
    request: str,
    settings,
    out: Path,
    limit: int,
    dry_run: bool,
    quiet: bool,
    allowed_domains: list[str],
) -> int:
    try:
        options = CliOptions(
            request=request,
            out_dir=out,
            limit=limit,
            dry_run=dry_run,
            allowed_domains=allowed_domains,
        )
        reporter = None if quiet else RichWorkflowReporter(console)
        result = KarAgent(settings, reporter=reporter).run(options)
    except (RuntimeError, ValidationError) as exc:
        console.print(f"[red]{exc}[/red]")
        return 2

    console.print(f"[bold]Intent:[/bold] {result.intent.search_intent}")
    if result.queries:
        console.print("[bold]Queries:[/bold] " + "; ".join(result.queries))

    if dry_run:
        _print_candidates(result.candidates)
    else:
        _print_downloads(result.downloaded)

    if result.errors:
        console.print("\n[yellow]Warnings[/yellow]")
        for error in result.errors:
            console.print(f"- {error}")

    if not result.candidates:
        console.print("[yellow]No direct .kar candidates found.[/yellow]")
        return 1
    if not dry_run and not result.downloaded:
        console.print("[yellow]No files were downloaded.[/yellow]")
        return 1
    return 0


class RichWorkflowReporter:
    def __init__(self, output: Console) -> None:
        self._console = output

    def __call__(self, event: WorkflowEvent) -> None:
        details = event.details
        if event.stage == "queries":
            self._console.print(f"[cyan]{event.message}[/cyan]")
            for index, query in enumerate(details.get("queries", []), start=1):
                self._console.print(f"  {index}. {query}")
            return

        suffix = _event_suffix(details)
        self._console.print(f"[cyan]{event.stage}[/cyan] {event.message}{suffix}")


def _event_suffix(details: dict[str, object]) -> str:
    parts: list[str] = []
    for key, value in details.items():
        if value is None or key == "queries":
            continue
        label = key.replace("_", " ")
        parts.append(f"{label}: {value}")
    return f" ({', '.join(parts)})" if parts else ""


def _print_candidates(candidates) -> None:
    table = Table(title="Ranked .kar Candidates", box=box.ASCII)
    table.add_column("Score", justify="right")
    table.add_column("URL")
    table.add_column("Reason")
    for candidate in candidates:
        table.add_row(f"{candidate.score:.2f}", candidate.url, candidate.reason)
    console.print(table)


def _print_downloads(downloaded) -> None:
    table = Table(title="Downloaded Files", box=box.ASCII)
    table.add_column("File")
    table.add_column("Bytes", justify="right")
    table.add_column("SHA256")
    for item in downloaded:
        table.add_row(item.filename, str(item.size_bytes), item.sha256[:12])
    console.print(table)


if __name__ == "__main__":
    app()
