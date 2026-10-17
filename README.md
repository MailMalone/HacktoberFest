# PR Triage

PR Triage gives open-source maintainers an evidence-backed first look at pull requests. It gathers public GitHub signals, asks Gemini to classify the contribution, and prints a recommendation with a respectful draft or private note. It never writes to GitHub.

## Run it

Requires Python 3.10+ and a Gemini API key. A GitHub token is optional but recommended.

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Add `GEMINI_API_KEY` to `.env`. Add `GITHUB_TOKEN` to raise the API rate limit. Start with one pull request:

```powershell
pr-triage triage pallets/flask --pr 123
```

Triage recent open PRs or print machine-readable JSON:

```powershell
pr-triage triage pallets/flask --limit 5
pr-triage triage pallets/flask --limit 5 --output json
pr-triage triage pallets/flask --limit 5 --output-file results.json
```

The first AI run explains what PR metadata goes to Google and asks for acknowledgment. Use `--accept-data-sharing` to acknowledge from a scripted run. The acknowledgment is stored locally in `~/.pr-triage/`.

## What the classifier sees

The evidence packet contains PR number, title, up to 2,000 characters of body, author, repository name, computed diff statistics, test and CI booleans, public contributor metadata, and linked issue labels/state. It never sends a GitHub token, raw diff patches, full issue bodies, or private repository metadata. Raw patches are inspected locally only to compute signals such as whitespace-only changes.

PR text is untrusted. The classifier prompt says to ignore instructions inside PR content, and detected prompt-injection attempts force `needs-review`. Missing or conflicting evidence, model errors, and confidence below 0.7 also fall back to `needs-review`.

## Verdicts

| Verdict | Meaning | Output |
| --- | --- | --- |
| `substantive` | Meaningful code or test work | Public comment draft |
| `minor` | Legitimate small change, such as a typo fix | Public comment draft |
| `suspected-spam` | Multiple independent, strong signals meet a defined pattern | Private maintainer note |
| `needs-review` | Evidence is incomplete, conflicting, or uncertain | Private maintainer note |

New accounts, first-time contributors, small diffs, documentation-only changes, and missing issue links are neutral on their own. Spam requires a combined pattern; uncertainty always goes to `needs-review`.

## Read-only guarantee

The client only issues GitHub `GET` requests. Comment drafts are printed locally. No comment, label, or close action exists in the CLI.

## Model and licensing

The default model is `gemini-2.5-flash`; set `GEMINI_MODEL` to another supported Gemini model. The application code is MIT licensed. Review the [Gemini API terms](https://ai.google.dev/gemini-api/terms.md) before submission. Gemini 2.5 Flash is a hosted model, so this default does not meet the event's open-weight model requirement; configure an eligible open-weight model for that challenge.

