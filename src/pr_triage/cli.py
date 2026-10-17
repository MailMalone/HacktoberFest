"""Typer command line interface for read-only pull request triage."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.text import Text

from pr_triage.github_client import GitHubAPIError
from pr_triage.models import TriageResult, Verdict
from pr_triage.privacy import ConsentError
from pr_triage.triage import triage_many, triage_recent

load_dotenv()
app = typer.Typer(help="Read-only, evidence-based triage for GitHub pull requests.")
console = Console()
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _validate_repo(repo: str) -> None:
    """Reject malformed repository selectors before creating requests."""
    if not REPO_PATTERN.fullmatch(repo):
        raise typer.BadParameter("Use owner/repo format, for example pallets/flask.")


def _render_table(results: list[TriageResult]) -> None:
    """Print a Rich table and local-only draft or note for each result."""
    table = Table(title="PR Triage", show_lines=True)
    table.add_column("PR", style="cyan", no_wrap=True)
    table.add_column("Verdict", no_wrap=True)
    table.add_column("Confidence", justify="right")
    table.add_column("Title", overflow="fold")
    table.add_column("Signals", overflow="fold")
    table.add_column("Reasoning", overflow="fold")

    styles = {
        Verdict.SUBSTANTIVE: "green",
        Verdict.MINOR: "blue",
        Verdict.SUSPECTED_SPAM: "red",
        Verdict.NEEDS_REVIEW: "yellow",
    }
    for result in results:
        evidence = result.evidence
        if evidence is None:
            signal_summary = "Unavailable"
        else:
            diff = evidence.diff
            issue = (
                f"issue #{evidence.issue.issue_number} ({evidence.issue.state})"
                if evidence.issue.linked
                else "no linked issue"
            )
            history = (
                f"{evidence.contributor.prior_pr_count} prior PRs"
                if evidence.contributor.prior_pr_count is not None
                else "history unavailable"
            )
            signal_summary = (
                f"{diff.files_changed} files · +{diff.additions}/-{diff.deletions} · "
                f"tests {'yes' if evidence.tests.tests_changed else 'no'} · {history} · {issue}"
            )
        table.add_row(
            f"#{result.pr_number}",
            Text(result.verdict.value, style=styles[result.verdict]),
            f"{result.confidence:.0%}",
            result.pr_title,
            signal_summary,
            result.reasoning,
        )
    console.print(table)

    for result in results:
        if result.drafted_comment:
            console.print(f"\n[bold]Public draft for #{result.pr_number}[/bold]")
            console.print(result.drafted_comment)
        elif result.maintainer_note:
            console.print(f"\n[bold]Private maintainer note for #{result.pr_number}[/bold]")
            console.print(result.maintainer_note)


def _emit(results: list[TriageResult], output: str, output_file: Path | None = None) -> None:
    """Write results to stdout as a table or JSON array."""
    payload = json.dumps([item.model_dump(mode="json") for item in results], indent=2)
    if output_file is not None:
        output_file.write_text(payload + "\n", encoding="utf-8")
        console.print(f"Saved JSON results to {output_file}", stderr=True)
    if output == "json":
        typer.echo(payload)
    else:
        _render_table(results)


def _run(
    repo: str,
    pr: int | None,
    limit: int,
    output: str,
    accept_data_sharing: bool,
    output_file: Path | None = None,
) -> None:
    _validate_repo(repo)
    token = os.getenv("GITHUB_TOKEN") or None
    api_key = os.getenv("GEMINI_API_KEY") or None
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    if pr is not None:
        if pr < 1:
            raise typer.BadParameter("PR number must be positive.")
        results = asyncio.run(triage_many(repo, [pr], token, api_key, model, accept_data_sharing))
    else:
        results = asyncio.run(
            triage_recent(repo, limit, token, api_key, model, accept_data_sharing)
        )
    _emit(results, output, output_file)


@app.command()
def triage(
    repo: Annotated[str, typer.Argument(help="GitHub repository in owner/repo format")],
    pr: Annotated[int | None, typer.Option("--pr", help="Triage one pull request number")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 10,
    output: Annotated[str, typer.Option("--output", help="table or json")] = "table",
    output_file: Annotated[
        Path | None,
        typer.Option("--output-file", help="Also save results as a JSON file."),
    ] = None,
    accept_data_sharing: Annotated[
        bool,
        typer.Option(
            "--accept-data-sharing",
            help="Acknowledge the one-time Google Gemini metadata notice.",
        ),
    ] = False,
) -> None:
    """Triage one pull request or the most recent open pull requests."""
    if output not in {"table", "json"}:
        raise typer.BadParameter("--output must be table or json.")
    try:
        _run(repo, pr, limit, output, accept_data_sharing, output_file)
    except ConsentError as exc:
        console.print(f"[yellow]{exc}[/yellow]", stderr=True)
        raise typer.Exit(code=2) from exc
    except GitHubAPIError as exc:
        console.print(f"[red]{exc}[/red]", stderr=True)
        raise typer.Exit(code=1) from exc


@app.command()
def demo(
    repo: Annotated[
        str, typer.Option("--repo", help="Public GitHub repository")
    ] = "firstcontributions/first-contributions",
    limit: Annotated[int, typer.Option("--limit", min=1, max=10)] = 3,
    output: Annotated[str, typer.Option("--output", help="table or json")] = "table",
    output_file: Annotated[
        Path | None,
        typer.Option("--output-file", help="Also save results as a JSON file."),
    ] = None,
    accept_data_sharing: Annotated[bool, typer.Option("--accept-data-sharing")] = False,
) -> None:
    """Run a short live demo against recent public pull requests."""
    if output not in {"table", "json"}:
        raise typer.BadParameter("--output must be table or json.")
    try:
        _run(repo, None, limit, output, accept_data_sharing, output_file)
    except ConsentError as exc:
        console.print(f"[yellow]{exc}[/yellow]", stderr=True)
        raise typer.Exit(code=2) from exc
    except GitHubAPIError as exc:
        console.print(f"[red]{exc}[/red]", stderr=True)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
