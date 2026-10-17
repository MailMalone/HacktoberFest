# PR Triage

PR Triage is a contribution review console for open-source maintainers. Review pull requests in batches, inspect repeated patches, and find assigned issues with no recent visible activity. AI assists PR classification; patch comparisons and inactivity checks run without an AI request. Maintainers inspect the evidence and make the final decision.

The web UI can publish a PR message after you edit and explicitly confirm it. Claim Monitor and all CLI commands are read-only.

## Run it

Requires Python 3.10+. Set an OpenRouter API key in `.env` for web and CLI classification. A GitHub token is optional but recommended.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Add `OPENROUTER_API_KEY` and optionally `GITHUB_TOKEN` to `.env`. The web UI reads these on the server; secrets never enter the browser. Start it with:

```powershell
.\.venv\Scripts\pr-triage.exe web
```

Open `http://127.0.0.1:8000`. These commands work without activating the virtual environment, including when PowerShell blocks activation scripts. Stop the server with Ctrl+C. To use the shorter `pr-triage` commands below, first activate the environment or substitute `.\.venv\Scripts\pr-triage.exe`.

Select a text model that supports structured outputs, enter a GitHub repository URL or `owner/repo`, then load pull requests. Enter a PR number to load only that PR; leave it blank to choose from the repository's open PRs. Select one or more and start triage. Expand any result for its evidence and detailed reason. The UI binds to loopback only.

The web console has four tabs: **Review PRs**, **Duplicate radar**, **Claim monitor**,
and **Connections**. Enter the repository once in the shared field above the tabs.
Choose the OpenRouter model and check GitHub API allowance in **Connections**.
Switching tabs preserves inputs and results. Use Left/Right or Home/End when a tab
has keyboard focus; each view also has a direct URL such as `/#radar` or `/#claims`.

The CLI remains available:

```powershell
pr-triage triage pallets/flask --pr 123
```

Triage recent open PRs or print machine-readable JSON:

```powershell
pr-triage triage pallets/flask --limit 5
pr-triage triage pallets/flask --limit 5 --output json
pr-triage triage pallets/flask --limit 5 --output-file results.json
```

The first CLI AI run explains what PR metadata goes to OpenRouter and asks for acknowledgment. Use `--accept-data-sharing` to acknowledge from a scripted run. The acknowledgment is stored locally in `~/.pr-triage/`. The web UI requires consent for each triage run. Provider retention and training policies vary.

## What the classifier sees

The evidence packet contains PR number, title, up to 2,000 characters of body, author, repository name, computed diff statistics, test and CI booleans, public contributor metadata, and linked issue labels/state. It never sends a GitHub token, raw diff patches, full issue bodies, or private repository metadata. Raw patches are inspected locally only to compute signals such as whitespace-only changes. OpenRouter forwards requests to the provider serving the selected model; review that provider's data policy before using private repository data.

PR text is untrusted. The classifier prompt says to ignore instructions inside PR content, and detected prompt-injection attempts force `needs-review`. Missing or conflicting evidence, model errors, and confidence below 0.7 also fall back to `needs-review`.

## Verdicts

| Verdict | Meaning | Output |
| --- | --- | --- |
| `substantive` | Meaningful code or test work | Public comment draft |
| `minor` | Legitimate small change, such as a typo fix | Public comment draft |
| `suspected-spam` | Multiple independent, strong signals meet a defined pattern | Private maintainer note |
| `needs-review` | Evidence is incomplete, conflicting, or uncertain | Private maintainer note |

New accounts, first-time contributors, small diffs, documentation-only changes, and missing issue links are neutral on their own. Spam requires a combined pattern; uncertainty always goes to `needs-review`.

## Duplicate radar and inactive claims

Open the **Duplicate contribution radar** section to compare the latest 30 PRs by
update time, including closed and merged PRs (configurable up to 100). The radar compares
changed lines with file paths and addition/deletion signs. It groups overlapping changes,
shows matching lines side by side, and lists differences. Similarity at or above 70% is
shown for inspection; it is not a spam verdict. Incomplete/truncated patches are excluded.
This detects repeated lines, not semantically equivalent rewrites. Maintainers can record
a legitimate-backport or other exception; exceptions are tied to the compared patch
fingerprints, so changed code needs fresh review. Raw patches stay local.

CLI radar output is also available as evidence JSON:

```powershell
.\.venv\Scripts\pr-triage.exe radar MailMalone/pr-triage-test-fixtures --limit 30 --output json
```

Click **Try offline demo** to exercise the real comparison and inactivity rules using six
synthetic PRs, duplicate clusters, a legitimate backport, and sample issue activity histories.
The demo needs no GitHub or AI calls and never enables a policy or changes remote data.

The **Inactive claim monitor** is read-only. Enter a repository, open **Claim monitor**,
choose **Minimum inactive days**, and click **Find inactive claimed issues**.
It lists explicitly assigned open issues with no qualifying recent GitHub activity,
showing the assignee, inactivity period, last observed activity, and issue link.
Assignee replies, assignments, and linked PR commits/reviews contribute to the activity check.
A recent metadata update conservatively counts as activity. Free-text claims alone are not tracked.
No visible activity does not prove someone stopped working; they may be working offline.

No background claim worker runs. The old run/reset routes reject requests, and previously
enabled policies cannot trigger warnings, closures, or assignment removals. Manual reviewed
PR comments in the Review PRs tab remain available. Claim scans need read access only.

For a real read-only example, enter `https://github.com/python-trio/trio` and use a
30-day threshold. On 2026-10-17 the scan returned four inactive assignments, including
[#240](https://github.com/python-trio/trio/issues/240) and
[#2258](https://github.com/python-trio/trio/issues/2258). Public repository activity can
change; these results are not an accusation that the assignees abandoned their work.

## GitHub token configuration

Set `GITHUB_TOKEN` in `.env` and restart the server. GitHub API requests use this server-side token; the web interface displays the remaining core API allowance and reset time. Authentication increases the allowance but does not remove primary, search, or secondary limits.

For posting, give a fine-grained personal access token access to the target repository with **Pull requests: Read and write** (or **Issues: Read and write**). In a result card, expand **Write a public PR message**, review or edit the message, then click **Post to GitHub** and confirm the destination and full message. Messages are posted as the token's GitHub user. Private maintainer notes are never automatically copied into public comments. After a delivery error, check GitHub before retrying to avoid duplicate comments.

The CLI does not post comments, apply labels, or close PRs.

## Terminal console

Table output shows a verdict summary and individual PR cards with evidence, reasoning,
and clearly separated public drafts or private notes. Interactive terminals show progress
while fetching evidence. JSON mode remains plain JSON for piping into other tools.

```powershell
# Readable radar report
.\.venv\Scripts\pr-triage.exe radar MailMalone/pr-triage-test-fixtures
# Find inactive assigned issues (uses the saved inactivity threshold, default 7 days)
.\.venv\Scripts\pr-triage.exe claims python-trio/trio
# Complete local walkthrough; no network calls
.\.venv\Scripts\pr-triage.exe demo --offline
# Machine-readable offline walkthrough
.\.venv\Scripts\pr-triage.exe demo --offline --output json
```

## Model and licensing

The CLI default is `meta-llama/llama-3.3-70b-instruct`; set `OPENROUTER_MODEL` to another supported model. The web UI lists text models that advertise structured-output support. OpenRouter routes each request to a model provider; availability and provider behavior can change. The application code is MIT licensed. Review OpenRouter and the selected provider's [privacy practices](https://openrouter.ai/privacy/).

## Scope and limitations

The application packages repeatable evidence collection, deterministic comparisons, batch review, and locally recorded duplicate exceptions into a dedicated workflow. A general-purpose coding assistant can perform similar investigations; this project makes those recurring checks available through a consistent interface. It does not introduce a new model or guarantee correct judgments.

- Model confidence is self-reported, not calibrated accuracy. Classification accuracy has not been established on an independently labelled benchmark.
- Duplicate Radar detects overlapping changed lines, not semantic equivalence. Intentional backports need human review.
- Claim Monitor uses explicit assignments and visible GitHub activity. It cannot establish a contributor's intent or see offline work.
- Scans have pagination limits and remain subject to GitHub rate limits. Incomplete activity evidence produces an error rather than a finding based on partial data.
- Issue handoff packages and automatic bottleneck diagnosis are future ideas, not implemented features.

## Development checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check src tests
node --check src/pr_triage/web/app.js
```

Tests cover conservative classification, patch comparisons, activity handling, blocked claim-write endpoints, CLI output, and web request validation. They verify implementation behavior rather than real-world model accuracy.

