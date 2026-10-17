"use strict";

const byId = (id) => document.getElementById(id);
const modelSelect = byId("model");
const statusLine = byId("status");
let models = [];
let pullRequests = [];
let selectedPullNumbers = new Set();
const MAX_SELECTED_PRS = 100;

function setStatus(message, kind = "") {
  statusLine.textContent = message;
  statusLine.className = `status ${kind}`;
}

async function postJson(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

function modelKey(model) {
  return model.id;
}

function selectedModel() {
  return models.find((model) => modelKey(model) === modelSelect.value);
}

async function loadModels() {
  const button = byId("reload-models");
  button.disabled = true;
  setStatus("Loading structured-output models from OpenRouter…");
  try {
    const data = await postJson("/api/models", {});
    models = data.models;
    renderModels();
    const message = models.length
      ? `${describeModels(models)} loaded.`
      : "No structured-output models are available.";
    setStatus(data.warnings.length ? `${message} ${data.warnings.join(" ")}` : message, models.length ? "ok" : "error");
  } catch (error) {
    models = [];
    renderModels();
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function describeModels(availableModels) {
  return `${availableModels.length} structured-output model${availableModels.length === 1 ? "" : "s"}`;
}

function renderModels() {
  const previous = modelSelect.value;
  modelSelect.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = models.length ? "Choose a model…" : "No models available";
  modelSelect.append(placeholder);

  const optgroup = document.createElement("optgroup");
  optgroup.label = "OpenRouter · structured-output models";
  for (const model of models) {
    const option = document.createElement("option");
    option.value = modelKey(model);
    option.textContent = `${model.name} · ${model.id}`;
    optgroup.append(option);
  }
  if (models.length) modelSelect.append(optgroup);
  if (models.some((model) => modelKey(model) === previous)) modelSelect.value = previous;
  renderSelectedModel();
}

function renderSelectedModel() {
  const meta = byId("model-meta");
  meta.replaceChildren();
  const model = selectedModel();
  if (!model) {
    addText(meta, "span", "Choose a structured-output model.", "model-count");
    return;
  }
  addText(meta, "span", `OpenRouter · ${model.name}`, "model-pill");
}

modelSelect.addEventListener("change", renderSelectedModel);
byId("reload-models").addEventListener("click", loadModels);

byId("load-pulls").addEventListener("click", async () => {
  const repo = byId("repo").value.trim();
  if (!repo) {
    setStatus("Enter a GitHub repository URL or owner/repo first.", "error");
    byId("repo").focus();
    return;
  }
  const button = byId("load-pulls");
  button.disabled = true;
  setStatus("Loading pull requests from GitHub…");
  try {
    const data = await postJson("/api/pulls", {
      repo,
      pr_number: byId("pr-number").value.trim() || null,
    });
    byId("repo").value = data.repo;
    pullRequests = data.pull_requests;
    selectedPullNumbers = new Set(
      byId("pr-number").value.trim() ? pullRequests.map((pull) => pull.number) : [],
    );
    renderPullRequests();
    setStatus(
      pullRequests.length === 1 && byId("pr-number").value.trim()
        ? `Loaded PR #${pullRequests[0].number}.`
        : `Loaded ${pullRequests.length} open pull requests. Select one or more to judge.`,
      "ok",
    );
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
  }
});

function renderPullRequests() {
  const list = byId("pull-list");
  list.replaceChildren();
  const selectAll = byId("select-all-pulls");
  selectAll.disabled = pullRequests.length === 0;
  selectAll.checked = pullRequests.length > 0 && selectedPullNumbers.size === pullRequests.length;
  selectAll.indeterminate = selectedPullNumbers.size > 0 && !selectAll.checked;
  byId("pull-count").textContent = pullRequests.length
    ? `${selectedPullNumbers.size}/${MAX_SELECTED_PRS} selected · ${pullRequests.length} open PRs available`
    : "Load pull requests to select";

  for (const pull of pullRequests) {
    const row = document.createElement("label");
    row.className = "pull-row";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selectedPullNumbers.has(pull.number);
    checkbox.disabled = !checkbox.checked && selectedPullNumbers.size >= MAX_SELECTED_PRS;
    checkbox.setAttribute("aria-label", `Select pull request ${pull.number}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked && selectedPullNumbers.size >= MAX_SELECTED_PRS) {
        setStatus(`You can judge up to ${MAX_SELECTED_PRS} pull requests in one batch.`, "error");
        renderPullRequests();
        return;
      }
      if (checkbox.checked) selectedPullNumbers.add(pull.number);
      else selectedPullNumbers.delete(pull.number);
      renderPullRequests();
    });
    const text = document.createElement("span");
    addText(text, "span", `#${pull.number} · ${pull.title}`, "pull-title");
    addText(text, "span", `by ${pull.author}`, "pull-subtitle");
    const link = document.createElement("a");
    link.href = pull.html_url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = "Open ↗";
    link.addEventListener("click", (event) => event.stopPropagation());
    row.append(checkbox, text, link);
    list.append(row);
  }
}

byId("select-all-pulls").addEventListener("change", (event) => {
  const requestedAll = event.currentTarget.checked;
  selectedPullNumbers = requestedAll
    ? new Set(pullRequests.slice(0, MAX_SELECTED_PRS).map((pull) => pull.number))
    : new Set();
  renderPullRequests();
  if (requestedAll && pullRequests.length > MAX_SELECTED_PRS) {
    setStatus(`Selected the first ${MAX_SELECTED_PRS}; choose a smaller batch to judge more later.`);
  }
});

byId("triage-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const model = selectedModel();
  if (!model) {
    setStatus("Choose a structured-output model first. Refresh the model list if needed.", "error");
    selectTab("connections");
    modelSelect.focus();
    return;
  }
  if (!selectedPullNumbers.size) {
    setStatus("Load pull requests and select at least one.", "error");
    return;
  }

  const submit = byId("submit");
  const numbers = [...selectedPullNumbers];
  submit.disabled = true;
  byId("results-section").classList.add("hidden");
  setStatus(`Judging ${numbers.length} pull request${numbers.length === 1 ? "" : "s"}. This can take a few minutes…`);
  try {
    const data = await postJson("/api/triage", {
      provider: "openrouter",
      model: model.id,
      repo: byId("repo").value.trim(),
      pr_numbers: numbers,
      consent: byId("consent").checked,
    });
    renderResults(data.results);
    byId("results-title").textContent = `Triage results · ${data.results.length} PRs`;
    byId("results-section").classList.remove("hidden");
    setStatus(`Finished judging ${data.results.length} pull requests. Open any result for details.`, "ok");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    submit.disabled = false;
  }
});

function addText(parent, tag, text, className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  parent.append(node);
  return node;
}

function shortReason(reason) {
  const firstSentence = reason.trim().split(/(?<=[.!?])\s+/)[0] || "Review the detailed evidence.";
  return firstSentence.length > 190 ? `${firstSentence.slice(0, 187)}…` : firstSentence;
}

function renderResults(results) {
  const container = byId("results");
  container.replaceChildren();
  for (const result of results) {
    const card = document.createElement("article");
    card.className = "result";
    const top = document.createElement("div");
    top.className = "result-top";
    addText(top, "span", result.verdict.replaceAll("-", " "), `verdict ${result.verdict}`);
    addText(top, "span", `Confidence score · ${Math.round(result.confidence * 100)}/100`, "score");
    card.append(top);

    const title = document.createElement("h3");
    const link = document.createElement("a");
    link.href = `https://github.com/${result.repo}/pull/${result.pr_number}`;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = `#${result.pr_number} · ${result.pr_title}`;
    title.append(link);
    card.append(title);
    addText(card, "p", shortReason(result.reasoning), "result-summary");
    addText(card, "p", `${result.repo} · @${result.author}`, "result-meta");

    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Open detailed reason and evidence";
    details.append(summary);
    addText(details, "p", result.reasoning, "result-reason");
    if (result.evidence) renderEvidence(details, result.evidence, result.signals_cited || []);
    if (result.maintainer_note) addText(details, "p", result.maintainer_note, "result-note");
    if (result.drafted_comment) addText(details, "p", `Draft comment: ${result.drafted_comment}`, "result-meta");
    card.append(details);
    renderCommentEditor(card, result);
    container.append(card);
  }
}

function renderCommentEditor(card, result) {
  const section = document.createElement("details");
  addText(section, "summary", "Write a public PR message");
  const label = document.createElement("label");
  label.textContent = `Message to ${result.repo} #${result.pr_number}`;
  const editor = document.createElement("textarea");
  editor.rows = 7;
  editor.maxLength = 65536;
  editor.value = result.drafted_comment || "";
  editor.placeholder = "Write a constructive message for the contributor…";
  editor.className = "comment-editor";
  label.append(editor);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "button";
  button.textContent = "Post to GitHub";
  const feedback = addText(section, "p", "Your message will be public and posted using the GitHub account associated with GITHUB_TOKEN. Private maintainer notes are not included.", "hint");
  feedback.setAttribute("role", "status");
  button.addEventListener("click", async () => {
    const body = editor.value.trim();
    if (!body) { feedback.textContent = "Write a message first."; return; }
    if (!window.confirm(`Post this public message to ${result.repo} PR #${result.pr_number}?\n\n${body}`)) return;
    button.disabled = true;
    feedback.textContent = "Posting…";
    try {
      const data = await postJson("/api/comment", { repo: result.repo, pr_number: result.pr_number, body, confirmed: true });
      editor.disabled = true;
      button.textContent = "Posted";
      feedback.textContent = "Message posted. ";
      const link = document.createElement("a");
      link.href = data.html_url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = "View on GitHub ↗";
      feedback.append(link);
    } catch (error) {
      feedback.textContent = error.message;
      button.disabled = false;
    }
  });
  section.append(label, button);
  card.append(section);
}

async function loadGitHubStatus() {
  const status = byId("github-status");
  try {
    const data = await postJson("/api/github/status", {});
    status.textContent = `${data.configured ? "GitHub token configured" : "No GitHub token — add GITHUB_TOKEN to .env and restart"} · ${data.rate_limit.remaining}/${data.rate_limit.limit} API requests remaining · resets ${new Date(data.rate_limit.reset * 1000).toLocaleTimeString()}`;
  } catch (error) { status.textContent = error.message; }
}

byId("refresh-github").addEventListener("click", loadGitHubStatus);
loadGitHubStatus();

function maintenanceRepo() {
  const repo = byId("maintenance-repo").value.trim() || byId("repo").value.trim();
  if (!repo) throw new Error("Enter a repository for radar and claim management.");
  byId("maintenance-repo").value = repo;
  return repo;
}

function prLink(parent, pr) {
  const link = document.createElement("a");
  link.href = pr.url;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = `#${pr.number} · ${pr.title} (${pr.state})`;
  parent.append(link);
}

function renderRadar(data, demo = false) {
  const container = byId("radar-results");
  container.replaceChildren();
  byId("radar-status").textContent = `${demo ? "OFFLINE DEMO · " : ""}${data.scanned} PRs scanned · ${data.matches.length} matching pairs · ${data.groups.length} groups. ${data.scope} ${data.warnings.join(" ")}`;
  for (const group of data.groups) addText(container, "p", `Related changes: ${group.map(n => `#${n}`).join(" ↔ ")}`, "model-pill");
  for (const match of data.matches) {
    const card = document.createElement("article");
    card.className = "result";
    addText(card, "h3", `${Math.round(match.similarity * 100)}% overlap · ${match.kind}`);
    prLink(card, match.left);
    addText(card, "span", " ↔ ");
    prLink(card, match.right);
    addText(card, "p", `${match.matching_lines} shared changed lines; ${match.left_lines} lines on the left and ${match.right_lines} on the right.`, "hint");
    const details = document.createElement("details");
    addText(details, "summary", "Inspect matching lines and differences");
    const table = document.createElement("table");
    table.style.cssText = "width:100%;table-layout:fixed;font:12px monospace;border-collapse:collapse";
    const header = document.createElement("tr");
    for (const name of ["File", `PR #${match.left.number}`, `PR #${match.right.number}`]) addText(header, "th", name);
    table.append(header);
    for (const row of match.evidence) {
      const tr = document.createElement("tr");
      for (const text of [row.file, row.left, row.right]) {
        const td = addText(tr, "td", text);
        td.style.cssText = "overflow-wrap:anywhere;white-space:pre-wrap;border-top:1px solid #ffffff22;padding:8px;vertical-align:top";
      }
      table.append(tr);
    }
    details.append(table);
    for (const [label, rows] of [["Only in left PR", match.left_only], ["Only in right PR", match.right_only]]) {
      if (rows.length) addText(details, "pre", `${label}\n${rows.map(row => `${row.file}: ${row.line}`).join("\n")}`, "result-reason");
    }
    addText(details, "p", "Evidence display is capped at 40 shared lines and 20 unique lines per PR. The score uses all available changed lines.", "hint");
    card.append(details);
    const exception = addText(card, "p", match.exception ? `Marked legitimate: ${match.exception}` : "No maintainer exception recorded.", "hint");
    if (!demo) {
      const reason = document.createElement("input");
      reason.placeholder = "Reason, e.g. legitimate backport";
      reason.setAttribute("aria-label", `Legitimate similarity reason for PRs ${match.left.number} and ${match.right.number}`);
      const button = addText(card, "button", "Mark similarity legitimate", "button");
      button.type = "button";
      card.append(reason);
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await postJson("/api/radar/exempt", {repo: data.repo, key: match.key, reason: reason.value});
          exception.textContent = `Marked legitimate: ${reason.value}`;
        } catch (error) { exception.textContent = error.message; }
        finally { button.disabled = false; }
      });
    }
    container.append(card);
  }
}

byId("scan-radar").addEventListener("click", async () => {
  const button = byId("scan-radar");
  button.disabled = true;
  byId("radar-status").textContent = "Fetching recent patches and comparing changes…";
  try {
    renderRadar(await postJson("/api/radar", {repo: maintenanceRepo(), limit: Number(byId("radar-limit").value)}));
  } catch (error) { byId("radar-status").textContent = error.message; }
  finally { button.disabled = false; }
});

function renderClaims(data, demo = false) {
  const container = byId("claims-results");
  container.replaceChildren();
  const claims = data.claims.filter(claim => claim.kind === "issue" && claim.idle_days >= data.policy.idle_days);
  byId("claims-status").textContent = `${demo ? "OFFLINE DEMO · " : ""}${claims.length} inactive assignments found · ${data.tracked ?? data.claims.length} assignments checked · read-only`;
  if (!claims.length) addText(container, "p", "No assigned issues meet this inactivity threshold.", "hint");
  for (const claim of claims) {
    const card = document.createElement("article");
    card.className = "result";
    addText(card, "h3", `Issue #${claim.number} · @${claim.claimant} · ${claim.idle_days} days inactive`);
    const link = document.createElement("a");
    link.href = claim.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = claim.title;
    card.append(link);
    addText(card, "p", `Latest observed activity: ${new Date(claim.last_activity).toLocaleString()}`, "hint");
    addText(card, "p", `No qualifying GitHub activity observed for at least ${data.policy.idle_days} days. Review the issue before concluding work has stopped.`, "result-note");
    container.append(card);
  }
  byId("claims-history").replaceChildren();
}

byId("preview-claims").addEventListener("click", async () => {
  const button = byId("preview-claims");
  button.disabled = true;
  try {
    const idle_days = Number(byId("claim-idle").value);
    if (!Number.isInteger(idle_days) || idle_days < 1 || idle_days > 365) throw new Error("Choose an inactivity threshold between 1 and 365 days.");
    byId("claims-status").textContent = "Checking issue assignments and contributor activity…";
    const data = await postJson("/api/claims", {repo: maintenanceRepo(), idle_days});
    renderClaims(data);
  } catch (error) { byId("claims-status").textContent = error.message; }
  finally { button.disabled = false; }
});

byId("maintenance-demo").addEventListener("click", async () => {
  try {
    const data = await postJson("/api/maintenance/demo", {});
    renderRadar(data.radar, true);
    renderClaims(data.claims, true);
  } catch (error) { byId("radar-status").textContent = error.message; }
});

function renderEvidence(parent, evidence, citedSignals) {
  const { diff, tests, issue, contributor } = evidence;
  const grid = document.createElement("div");
  grid.className = "detail-grid";
  const changedFiles = diff.files_changed === 0 && !diff.patch_coverage_complete
    ? "Unavailable"
    : `${diff.files_changed} ${diff.files_changed === 1 ? "file" : "files"} · +${diff.additions} ${diff.additions === 1 ? "line" : "lines"} / −${diff.deletions} ${diff.deletions === 1 ? "line" : "lines"}`;
  const items = [
    ["Changed files", changedFiles],
    ["Tests and CI", `${tests.tests_changed ? "Tests changed" : "No test changes"} · ${tests.ci_changed ? "CI changed" : "CI unchanged"}`],
    ["Linked issue", issue.linked ? `#${issue.issue_number} · ${issue.state || "state unknown"}` : "None detected"],
    ["Contributor history", contributor.prior_pr_count === null ? "Unavailable" : `${contributor.prior_pr_count} earlier PRs`],
  ];
  for (const [label, value] of items) {
    const item = document.createElement("div");
    item.className = "detail-item";
    addText(item, "span", label, "detail-label");
    addText(item, "span", value, "detail-value");
    grid.append(item);
  }
  parent.append(grid);
  if (citedSignals.length) addText(parent, "p", `Evidence fields: ${citedSignals.join(", ")}`, "result-meta");
}

const featureTabs = [...document.querySelectorAll('[role="tab"][data-tab]')];

function selectTab(name, focus = false) {
  const selected = featureTabs.find(tab => tab.dataset.tab === name) || featureTabs[0];
  for (const tab of featureTabs) {
    const active = tab === selected;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    byId(tab.getAttribute("aria-controls")).hidden = !active;
  }
  history.replaceState(null, "", `#${selected.dataset.tab}`);
  if (focus) selected.focus();
}

for (const tab of featureTabs) {
  tab.addEventListener("click", () => selectTab(tab.dataset.tab));
  tab.addEventListener("keydown", event => {
    const index = featureTabs.indexOf(tab);
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % featureTabs.length;
    else if (event.key === "ArrowLeft") next = (index + featureTabs.length - 1) % featureTabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = featureTabs.length - 1;
    else return;
    event.preventDefault();
    selectTab(featureTabs[next].dataset.tab, true);
  });
}

document.querySelectorAll("[data-open-tab]").forEach(button => {
  button.addEventListener("click", () => selectTab(button.dataset.openTab, true));
});
window.addEventListener("hashchange", () => selectTab(location.hash.slice(1)));

byId("maintenance-repo").addEventListener("input", () => {
  const value = byId("maintenance-repo").value.trim();
  if (byId("repo").value !== value) {
    byId("repo").value = value;
    pullRequests = [];
    selectedPullNumbers.clear();
    renderPullRequests();
    byId("results-section").classList.add("hidden");
  }
});
byId("maintenance-repo").addEventListener("keydown", event => {
  if (event.key === "Enter") {
    event.preventDefault();
    selectTab("review");
    byId("load-pulls").click();
  }
});

selectTab(location.hash.slice(1));
loadModels();
