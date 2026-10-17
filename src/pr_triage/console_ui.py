"""Readable terminal views with literal rendering of untrusted GitHub text."""

from collections import Counter

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from pr_triage.models import TriageResult, Verdict

VERDICT_COLORS = {
    Verdict.SUBSTANTIVE: "bright_green",
    Verdict.MINOR: "bright_cyan",
    Verdict.NEEDS_REVIEW: "yellow",
    Verdict.SUSPECTED_SPAM: "bright_red",
}


def banner(console: Console, subtitle: str) -> None:
    """Print the console identity with a task-specific subtitle."""
    console.print(
        Panel(
            Group(
                Text("PR TRIAGE  /  THE CONTRIBUTION DESK", style="bold bright_blue"),
                Text(subtitle, style="dim"),
            ),
            border_style="blue",
            padding=(1, 2),
        )
    )


def render_results(console: Console, results: list[TriageResult]) -> None:
    """Show verdict totals and individual evidence cards at any terminal width."""
    banner(console, "Review results | recommendations for maintainer judgment")
    if not results:
        console.print("No pull requests found.", style="dim")
        return
    counts = Counter(result.verdict for result in results)
    totals = Text(f"{len(results)} PRs reviewed  ")
    for verdict in Verdict:
        totals.append(
            f" {counts[verdict]} {verdict.value.replace('-', ' ')} ", style=VERDICT_COLORS[verdict]
        )
    console.print(totals)
    console.print()
    for result in results:
        color = VERDICT_COLORS[result.verdict]
        verdict = Text(result.verdict.value.upper().replace("-", " "), style=f"bold {color}")
        verdict.append(f"   Model confidence {result.confidence:.0%}", style="dim")
        content = [
            Text(result.pr_title, style="bold"),
            Text(f"{result.repo} | @{result.author}", style="dim"),
            verdict,
            Text(""),
        ]
        if result.evidence:
            e = result.evidence
            content.append(
                Text(
                    f"{e.diff.files_changed} files | +{e.diff.additions} / -{e.diff.deletions} | "
                    f"Tests {'changed' if e.tests.tests_changed else 'unchanged'} | "
                    f"Issue {'#' + str(e.issue.issue_number) if e.issue.linked else 'not linked'}",
                    style="bright_cyan",
                )
            )
        content.extend([Text(result.reasoning), Text("")])
        if result.drafted_comment:
            content.append(
                Panel(
                    Text(result.drafted_comment), title="Public comment draft", border_style="dim"
                )
            )
        elif result.maintainer_note:
            content.append(
                Panel(
                    Text(result.maintainer_note),
                    title="Private maintainer note",
                    border_style="yellow",
                )
            )
        console.print(
            Panel(
                Group(*content),
                title=f"PR #{result.pr_number}",
                title_align="left",
                border_style=color,
                padding=(1, 2),
            )
        )
    console.print(
        "Confidence is model-reported, not measured accuracy. Comments shown here are drafts.",
        style="dim",
    )


def render_radar(console: Console, data: dict) -> None:
    """Render matching PR pairs and a short evidence excerpt."""
    banner(console, f"Duplicate radar | {data['repo']}")
    console.print(
        f"{data['scanned']} scanned | {len(data['matches'])} matching pairs | {len(data['groups'])} groups",
        style="bright_cyan",
    )
    console.print(Text(data["scope"], style="dim"))
    for match in data["matches"]:
        table = Table(box=box.SIMPLE, expand=True, show_header=False)
        table.add_column("PR", width=8)
        table.add_column("Change", ratio=1)
        table.add_column("State", width=8)
        for side in ("left", "right"):
            pr = match[side]
            table.add_row(f"#{pr['number']}", Text(pr["title"]), Text(pr["state"]))
        lines = [table, Text(f"{match['matching_lines']} shared changed lines | {match['kind']}")]
        for row in match["evidence"][:3]:
            lines.append(Text(f"{row['file']}: {row['left']}", style="dim"))
        if match.get("exception"):
            lines.append(Text(f"Maintainer exception: {match['exception']}", style="bright_green"))
        console.print(
            Panel(
                Group(*lines),
                title=f"{match['similarity']:.0%} overlap",
                border_style="blue",
                title_align="left",
            )
        )
    if not data["matches"]:
        console.print("No matching changes within the scanned PRs.", style="dim")
    for warning in data["warnings"]:
        console.print(Text(warning, style="yellow"))
    console.print(
        "Similarity is not a spam verdict. Use --output json for full line evidence.", style="dim"
    )


def render_claims(console: Console, data: dict) -> None:
    """Show read-only claim states and the current saved policy."""
    policy = data["policy"]
    banner(console, "Claim monitor | read-only preview")
    console.print(
        f"Read-only | Minimum {policy['idle_days']} inactive days",
        style="bright_cyan",
    )
    for claim in data["claims"]:
        if claim["kind"] != "issue" or claim["idle_days"] < policy["idle_days"]:
            continue
        console.print(
            Panel(
                Group(
                    Text(f"{claim['title']} | @{claim['claimant']}", style="bold"),
                    Text(
                        f"{claim['idle_days']} days inactive | {claim['status'].upper()}",
                        style="yellow",
                    ),
                    Text(f"Last observed activity: {claim['last_activity']}", style="dim"),
                    Text(claim["url"], style="blue"),
                ),
                title=f"{claim['kind'].upper()} #{claim['number']}",
                title_align="left",
                border_style="blue",
            )
        )
    if not data["claims"]:
        console.print("No eligible claims found.", style="dim")
    console.print(
        "Read-only. Missing visible activity does not prove work has stopped.", style="dim"
    )
