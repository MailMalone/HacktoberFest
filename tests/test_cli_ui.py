import io
import json

from rich.console import Console
from typer.testing import CliRunner

from pr_triage import cli
from pr_triage.console_ui import render_results
from pr_triage.triage import unavailable_result

runner = CliRunner()


def test_offline_demo_json_is_clean_and_network_independent():
    result = runner.invoke(cli.app, ["demo", "--offline", "--output", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["radar"]["scanned"] == 6
    assert data["claims"]["policy"]["enabled"] is False


def test_triage_json_does_not_include_terminal_decoration(monkeypatch):
    async def recent(*args):
        return [unavailable_result("owner/repo", 1, "Missing evidence.")]

    monkeypatch.setattr(cli, "triage_recent", recent)
    result = runner.invoke(cli.app, ["triage", "owner/repo", "--output", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data[0]["verdict"] == "needs-review"


def test_terminal_cards_render_untrusted_markup_literally_at_narrow_width():
    result = unavailable_result("owner/repo", 1, "Keep [red]literal[/red] evidence.")
    result.pr_title = "[link=https://untrusted.example]Fix[/link]"
    output = io.StringIO()
    render_results(Console(file=output, width=60, color_system=None), [result])
    assert "[red]literal[/red]" in output.getvalue()
    assert "Private maintainer note" in output.getvalue()
    assert "PR #1" in output.getvalue()


def test_cli_help_exposes_each_feature():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for command in ("triage", "radar", "claims", "web", "demo"):
        assert command in result.output
