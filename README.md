# PR Triage

PR Triage gives open-source maintainers an evidence-backed first look at pull requests. It gathers public GitHub signals, asks an OpenRouter model to classify the contribution, and prints a recommendation with a respectful draft or private note. It never writes to GitHub.

## Run it

Requires Python 3.10+. Enter an OpenRouter API key in the web UI for AI classification. A GitHub token is optional but recommended.

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

For CLI use, add `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` to `.env`. Add `GITHUB_TOKEN` to raise the GitHub API rate limit. Start the web UI without saving your OpenRouter key:

```powershell
pr-triage web
```

Open `http://127.0.0.1:8000`. Enter your OpenRouter key, load and select a model, choose a repository and PR number, then review the data-sharing notice. The key stays in the browser tab and request memory; the app does not save it. The UI binds to loopback only. Select a model that supports structured outputs; the model catalog is filtered for that capability. Check event rules to confirm your chosen model qualifies as open-weight.

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

## Read-only guarantee

The client only issues GitHub `GET` requests. Comment drafts are printed locally. No comment, label, or close action exists in the CLI.

## Model and licensing

The CLI default is `meta-llama/llama-3.3-70b-instruct`; set `OPENROUTER_MODEL` to another supported model. The web UI lists models that advertise structured-output support. OpenRouter routes each request to a model provider; availability and provider behavior can change. The application code is MIT licensed. Review OpenRouter and the selected provider's [privacy practices](https://openrouter.ai/privacy/). Confirm the selected model and provider meet your event's open-weight rules.

