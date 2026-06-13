from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from kar_scraper.agent import KarAgent
from kar_scraper.config import get_settings
from kar_scraper.models import CliOptions

app = typer.Typer(help="Find and download public direct .kar karaoke files.")
console = Console()


@app.command()
def main(
    request: Annotated[str, typer.Argument(help="Artist, song, genre, or free-text karaoke request.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")] = Path("downloads"),
    limit: Annotated[int, typer.Option("--limit", "-l", min=1, max=50, help="Maximum accepted candidates/downloads.")] = 10,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show candidates without downloading.")] = False,
    allowed_domain: Annotated[
        list[str] | None,
        typer.Option("--allowed-domain", help="Restrict direct .kar links to this domain. Can be repeated."),
    ] = None,
) -> None:
    settings = get_settings()
    options = CliOptions(
        request=request,
        out_dir=out,
        limit=limit,
        dry_run=dry_run,
        allowed_domains=allowed_domain or [],
    )
    try:
        result = KarAgent(settings).run(options)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

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
        raise typer.Exit(code=1)
    if not dry_run and not result.downloaded:
        console.print("[yellow]No files were downloaded.[/yellow]")
        raise typer.Exit(code=1)


def _print_candidates(candidates) -> None:
    table = Table(title="Ranked .kar Candidates")
    table.add_column("Score", justify="right")
    table.add_column("URL")
    table.add_column("Reason")
    for candidate in candidates:
        table.add_row(f"{candidate.score:.2f}", candidate.url, candidate.reason)
    console.print(table)


def _print_downloads(downloaded) -> None:
    table = Table(title="Downloaded Files")
    table.add_column("File")
    table.add_column("Bytes", justify="right")
    table.add_column("SHA256")
    for item in downloaded:
        table.add_row(item.filename, str(item.size_bytes), item.sha256[:12])
    console.print(table)


if __name__ == "__main__":
    app()

