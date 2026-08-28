// The panel asks the content script for what is on screen, sends it to the local bridge,
// and renders the answer. It has no other outbound call: nothing here contacts a job site.

const $ = (id) => document.getElementById(id);
const state = { endpoint: "http://127.0.0.1:8787", token: "" };

async function loadSettings() {
  const saved = await chrome.storage.local.get(["endpoint", "token"]);
  if (saved.endpoint) state.endpoint = saved.endpoint;
  if (saved.token) state.token = saved.token;
  $("endpoint").value = state.endpoint;
  $("token").value = state.token;
  checkHealth();
}

async function checkHealth() {
  try {
    const response = await fetch(`${state.endpoint}/health`);
    const body = await response.json();
    $("health").textContent = body.store_enabled
      ? "bridge up — storing enabled"
      : "bridge up — read only";
  } catch {
    $("health").textContent = "bridge unreachable; start assist_bridge.py";
  }
}

$("save").addEventListener("click", async () => {
  state.endpoint = $("endpoint").value.trim().replace(/\/$/, "");
  state.token = $("token").value.trim();
  await chrome.storage.local.set({ endpoint: state.endpoint, token: state.token });
  checkHealth();
});

function tag(decision) {
  const cls = decision === "match" ? "match" : decision === "fail" ? "fail" : "review";
  return `<span class="tag ${cls}">${decision}</span>`;
}

function render(result) {
  $("job-title").textContent = result.job.title || "(title not read)";
  $("job-meta").textContent = [result.job.employer, result.job.location,
                               result.job.work_arrangement].filter(Boolean).join(" · ");
  $("directions").innerHTML = result.directions.map((d) => {
    const reasons = [
      ...(d.hard_failures || []),
      ...(d.review_reasons || [])
    ].slice(0, 3).join(", ");
    const warn = (d.warning_terms_required || []).length
      ? `required warning terms: ${d.warning_terms_required.join(", ")}`
      : "";
    return `<li>${tag(d.decision)}<span class="name">${d.name || d.direction_id}
      <span class="reasons">${[reasons, warn].filter(Boolean).join(" — ")}</span></span>
      <span class="score">${d.ranking_score ?? ""}</span></li>`;
  }).join("");
  $("evidence").innerHTML = (result.evidence.matches || []).map((m) => {
    const cls = m.strength === "none" ? "fail"
      : (m.strength === "direct" || m.strength === "strongly_related") ? "match" : "review";
    return `<li><span class="tag ${cls}">${m.strength}</span>
      <span class="name">${m.requirement}</span></li>`;
  }).join("") || "<li class='muted'>no stated requirements were read</li>";
  $("gap").textContent = result.evidence.main_gap
    ? `main gap: ${result.evidence.main_gap}` : "";
  $("notice").textContent = result.notice || "";
  $("result").hidden = false;
}

$("read").addEventListener("click", async () => {
  $("status").textContent = "reading the open posting…";
  $("result").hidden = true;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    // The user's current tab is the only page this extension ever reads.
    const page = await chrome.tabs.sendMessage(tab.id, { type: "jobloom:read-visible-posting" });
    if (!page?.ok) throw new Error(page?.error || "this page did not return a posting");
    const response = await fetch(`${state.endpoint}/positioning`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Jobloom-Token": state.token },
      body: JSON.stringify(page.page)
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `bridge returned ${response.status}`);
    $("status").textContent = page.page.container === "fallback_body"
      ? "read from the whole page; the posting pane was not recognised"
      : "";
    render(body);
  } catch (error) {
    $("status").textContent = String(error.message || error);
  }
});

loadSettings();
