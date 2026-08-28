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

// activeTab is granted by clicking the toolbar action, which a click inside this panel is
// not, and LinkedIn revokes it again on every in-app navigation. So page access is an
// explicit, optional, revocable grant the user makes once in Chrome's own dialog.
const JOB_HOSTS = ["https://www.linkedin.com/*", "https://*.indeed.com/*"];

async function hasPageAccess() {
  return chrome.permissions.contains({ origins: JOB_HOSTS });
}

async function refreshAccess() {
  const granted = await hasPageAccess();
  $("access-state").textContent = granted
    ? "page access granted — revoke any time in chrome://extensions"
    : "not granted; Jobloom cannot read a posting until you allow it";
  $("grant").hidden = granted;
  $("read").disabled = !granted;
}

$("grant").addEventListener("click", async () => {
  // Must be called straight from the user's click for Chrome to show the dialog.
  try {
    await chrome.permissions.request({ origins: JOB_HOSTS });
  } catch (error) {
    $("access-state").textContent = String(error.message || error);
  }
  refreshAccess();
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

// Injected into the tab only when the user presses the button. Nothing from this
// extension runs on a job site before that press, and the injection reads the text the
// page has already rendered — it does not fetch, follow, or expand anything.
function readVisiblePosting() {
  const CONTAINERS = [
    ".jobs-search__job-details--wrapper",
    ".jobs-search__job-details--container",
    ".jobs-details__main-content",
    ".jobs-details",
    ".job-view-layout",
    "#jobsearch-ViewjobPaneWrapper",
    ".jobsearch-JobComponent",
    "main"
  ];
  let pane = document.body;
  let matched = "fallback_body";
  for (const selector of CONTAINERS) {
    const node = document.querySelector(selector);
    if (node && (node.innerText || "").trim().length > 400) { pane = node; matched = selector; break; }
  }
  let text = (pane.innerText || "").replace(/\n{3,}/g, "\n\n").trim();
  const heading = pane.querySelector("h1, h2");
  const title = (heading?.innerText || document.title || "").trim();
  // A search page keeps the result list and the open posting in one container. When the
  // title appears inside the text, everything before it is the list, so drop it.
  if (title && matched === "main" || matched === "fallback_body") {
    const start = text.indexOf(title);
    if (title && start > 200) { text = text.slice(start); matched += "+sliced_at_title"; }
  }
  return { url: location.href.split("?")[0], text: text.slice(0, 60000), title, container: matched };
}

$("read").addEventListener("click", async () => {
  $("status").textContent = "reading the open posting…";
  $("result").hidden = true;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) throw new Error("no active tab");
    if (!(await hasPageAccess())) throw new Error("page access not granted yet");
    // activeTab is granted by the user's own click, and covers this tab only.
    const [injected] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: readVisiblePosting
    });
    const page = injected?.result;
    if (!page?.text) throw new Error("this page did not return a posting");
    const response = await fetch(`${state.endpoint}/positioning`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Jobloom-Token": state.token },
      body: JSON.stringify(page)
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `bridge returned ${response.status}`);
    $("status").textContent = page.container === "fallback_body"
      ? `read the whole page; the posting pane was not recognised (${page.text.length} chars)`
      : `read from ${page.container}`;
    render(body);
  } catch (error) {
    $("status").textContent = String(error.message || error);
  }
});

loadSettings();
refreshAccess();
