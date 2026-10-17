"use strict";

const byId = (id) => document.getElementById(id);
const apiKey = byId("api-key");
const modelSelect = byId("model");
const statusLine = byId("status");
let models = [];

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

byId("load-models").addEventListener("click", async () => {
  if (!apiKey.value.trim()) {
    setStatus("Enter OpenRouter API key first.", "error");
    apiKey.focus();
    return;
  }
  setStatus("Loading supported models…");
  byId("load-models").disabled = true;
  try {
    const data = await postJson("/api/models", { api_key: apiKey.value.trim() });
    models = data.models;
    renderModelOptions();
    setStatus(`${models.length} structured-output models loaded.`, "ok");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    byId("load-models").disabled = false;
  }
});

byId("model-search").addEventListener("input", renderModelOptions);

function renderModelOptions() {
  const query = byId("model-search").value.trim().toLowerCase();
  const previous = modelSelect.value;
  const visible = models.filter((item) => `${item.name} ${item.id}`.toLowerCase().includes(query));
  modelSelect.replaceChildren();
  for (const item of visible) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = `${item.name} · ${item.id}`;
    modelSelect.append(option);
  }
  if (visible.some((item) => item.id === previous)) modelSelect.value = previous;
  if (!visible.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = models.length ? "No matching models" : "Load models with your API key";
    modelSelect.append(option);
  }
}

byId("clear-key").addEventListener("click", () => {
  apiKey.value = "";
  apiKey.focus();
  setStatus("Key cleared from the tab.");
});

byId("triage-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!modelSelect.value) {
    setStatus("Load models and select one first.", "error");
    return;
  }
  const submit = byId("submit");
  submit.disabled = true;
  setStatus("Fetching GitHub evidence and classifying…");
  byId("results-section").classList.add("hidden");
  try {
    const data = await postJson("/api/triage", {
      api_key: apiKey.value.trim(),
      model: modelSelect.value,
      repo: byId("repo").value.trim(),
      pr_number: Number(byId("pr-number").value),
      github_token: byId("github-token").value.trim(),
      consent: byId("consent").checked,
    });
    renderResults(data.results);
    byId("results-section").classList.remove("hidden");
    setStatus("Triage complete.", "ok");
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

function renderResults(results) {
  const container = byId("results");
  container.replaceChildren();
  for (const result of results) {
    const card = document.createElement("article");
    card.className = "result";
    const top = document.createElement("div");
    top.className = "result-top";
    addText(top, "span", result.verdict, `verdict ${result.verdict}`);
    addText(top, "span", `${Math.round(result.confidence * 100)}% confidence`, "confidence");
    card.append(top);
    addText(card, "h3", `#${result.pr_number} · ${result.pr_title}`);
    addText(card, "p", result.reasoning);
    addText(card, "p", `Author: ${result.author} · Repo: ${result.repo}`);
    if (result.evidence) {
      const { diff, tests, issue, contributor } = result.evidence;
      const issueText = issue.linked ? `issue #${issue.issue_number} (${issue.state || "state unknown"})` : "no linked issue";
      const history = contributor.prior_pr_count === null ? "history unavailable" : `${contributor.prior_pr_count} prior PRs`;
      addText(card, "p", `${diff.files_changed} files · +${diff.additions}/-${diff.deletions} · tests ${tests.tests_changed ? "changed" : "unchanged"} · ${history} · ${issueText}`);
    }
    if (result.signals_cited?.length) addText(card, "p", `Evidence fields: ${result.signals_cited.join(", ")}`);
    if (result.maintainer_note) {
      addText(card, "p", `Private maintainer note: ${result.maintainer_note}`);
    }
    if (result.drafted_comment) {
      const comment = document.createElement("p");
      const label = document.createElement("strong");
      label.textContent = "Public comment draft: ";
      comment.append(label, document.createTextNode(result.drafted_comment));
      card.append(comment);
    }
    container.append(card);
  }
}
