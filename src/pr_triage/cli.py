"""Typer command line interface for read-only pull request triage."""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import nullcontext
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console

from pr_triage.console_ui import render_claims, render_radar, render_results
from pr_triage.github_client import GitHubAPIError
from pr_triage.models import TriageResult
from pr_triage.privacy import ConsentError
from pr_triage.triage import triage_many, triage_recent

load_dotenv()
app = typer.Typer(
    help="""[bold blue]PR TRIAGE[/bold blue] · The contribution desk.

Review PRs, trace duplicate changes, and preview abandoned claims.""",
    no_args_is_help=True,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
    epilog="Start the browser console: pr-triage web  |  Offline walkthrough: pr-triage demo --offline",
)
console = Console()
error_console = Console(stderr=True)
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _validate_repo(repo: str) -> None:
    """Reject malformed repository selectors before creating requests."""
    if not REPO_PATTERN.fullmatch(repo):
        raise typer.BadParameter("Use owner/repo format, for example pallets/flask.")


def _render_table(results: list[TriageResult]) -> None:
    """Render readable verdict cards with structured evidence."""
    render_results(console, results)


def _emit(results: list[TriageResult], output: str, output_file: Path | None = None) -> None:
    """Write results to stdout as a table or JSON array."""
    payload = json.dumps([item.model_dump(mode="json") for item in results], indent=2)
    if output_file is not None:
        output_file.write_text(payload + "\n", encoding="utf-8")
        error_console.print(f"Saved JSON results to {output_file}", markup=False)
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
    api_key = os.getenv("OPENROUTER_API_KEY") or None
    model = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct")
    with (
        console.status(
            "Collecting GitHub evidence and classifying PRs…", spinner="dots", spinner_style="blue"
        )
        if output != "json" and console.is_terminal
        else nullcontext()
    ):
        if pr is not None:
            if pr < 1:
                raise typer.BadParameter("PR number must be positive.")
            results = asyncio.run(
                triage_many(repo, [pr], token, api_key, model, accept_data_sharing)
            )
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
            help="Acknowledge the one-time OpenRouter metadata notice.",
        ),
    ] = False,
) -> None:
    """Triage one pull request or the most recent open pull requests."""
    if output not in {"table", "json"}:
        raise typer.BadParameter("--output must be table or json.")
    try:
        _run(repo, pr, limit, output, accept_data_sharing, output_file)
    except ConsentError as exc:
        error_console.print(str(exc), style="yellow", markup=False)
        raise typer.Exit(code=2) from exc
    except GitHubAPIError as exc:
        error_console.print(str(exc), style="red", markup=False)
        raise typer.Exit(code=1) from exc


@app.command()
def demo(
    offline: Annotated[
        bool, typer.Option("--offline", help="Run a local demo without API calls.")
    ] = False,
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
    if offline:
        from pr_triage.maintenance_demo import build_demo

        data = asyncio.run(build_demo())
        if output == "json":
            typer.echo(json.dumps(data, indent=2))
        else:
            render_radar(console, data["radar"])
            render_claims(console, data["claims"])
        if output_file is not None:
            output_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return
    if output not in {"table", "json"}:
        raise typer.BadParameter("--output must be table or json.")
    try:
        _run(repo, None, limit, output, accept_data_sharing, output_file)
    except ConsentError as exc:
        error_console.print(str(exc), style="yellow", markup=False)
        raise typer.Exit(code=2) from exc
    except GitHubAPIError as exc:
        error_console.print(str(exc), style="red", markup=False)
        raise typer.Exit(code=1) from exc


@app.command()
def radar(
    repo: Annotated[str, typer.Argument(help="Repository in owner/repo format")],
    limit: Annotated[int, typer.Option(min=1, max=100)] = 30,
    output: Annotated[str, typer.Option(help="table or json")] = "table",
) -> None:
    """Find matching changes across recent and historical PRs; show cards or JSON."""
    from pr_triage.github_client import GitHubClient
    from pr_triage.radar import scan_radar

    _validate_repo(repo)
    if output not in {"table", "json"}:
        raise typer.BadParameter("--output must be table or json.")

    async def run():
        async with GitHubClient(token=os.getenv("GITHUB_TOKEN")) as client:
            return await scan_radar(client, repo, limit)

    try:
        with (
            console.status("Comparing recent patches…", spinner_style="blue")
            if output != "json" and console.is_terminal
            else nullcontext()
        ):
            data = asyncio.run(run())
        if output == "json":
            typer.echo(json.dumps(data, indent=2))
        else:
            render_radar(console, data)
    except GitHubAPIError as exc:
        error_console.print(str(exc), style="red", markup=False)
        raise typer.Exit(1) from exc


@app.command()
def claims(
    repo: Annotated[str, typer.Argument(help="Repository in owner/repo format")],
    output: Annotated[str, typer.Option(help="table or json")] = "table",
) -> None:
    """Preview the saved claim policy and contributor activity without changing GitHub."""
    from pr_triage.github_client import GitHubClient
    from pr_triage.maintenance import ClaimPolicy, StateStore, inactive_issues

    _validate_repo(repo)
    if output not in {"table", "json"}:
        raise typer.BadParameter("--output must be table or json.")
    store = StateStore()
    policy = ClaimPolicy.model_validate(store.get(f"policy:{repo.lower()}", {}))

    async def run() -> dict:
        async with GitHubClient(token=os.getenv("GITHUB_TOKEN")) as client:
            return await inactive_issues(client, repo.lower(), policy, store)

    try:
        with (
            console.status("Checking claim activity…", spinner_style="blue")
            if output != "json" and console.is_terminal
            else nullcontext()
        ):
            data = asyncio.run(run())
        if output == "json":
            typer.echo(json.dumps(data, indent=2))
        else:
            render_claims(console, data)
    except (GitHubAPIError, ValueError, KeyError) as exc:
        error_console.print(str(exc), style="red", markup=False)
        raise typer.Exit(1) from exc


@app.command()
def web(
    host: Annotated[str, typer.Option(help="Bind address; loopback only.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8000,
) -> None:
    """Start the local browser interface."""
    from pr_triage.webapp import serve

    try:
        serve(host=host, port=port)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


if __name__ == "__main__":
    app()
