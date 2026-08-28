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

const VERDICT_TEXT = {
  apply: ["Worth applying", "ok"],
  review: ["Worth a look", "warn"],
  stretch: ["A stretch", "warn"],
  skip: ["Probably skip", "bad"]
};

// Four ways a requirement can stand, each asking for a different move. A keyword counter
// merges them and so rewards padding; keeping them apart is the point of this panel.
const CLASS_VIEW = [
  ["hidden_strength", "You have done this — the resume does not show it", "ok",
   "add it; this is your own confirmed work"],
  ["evidence_gap", "Shown, but thin", "warn",
   "no figure or outcome attached; worth strengthening"],
  ["transferable", "Related, not the same thing", "warn",
   "say it as adjacent work — it never becomes direct experience"],
  ["real_gap", "You have not done this", "bad",
   "leave it out; a stretch is honest, an invention is not"],
  ["covered", "Covered and already shown", "ok", "nothing to do"]
];

function render(result) {
  const [label, cls] = VERDICT_TEXT[result.verdict.call] || ["Unclear", "warn"];
  $("verdict").className = `verdict ${cls}`;
  $("verdict").innerHTML = `<strong>${label}</strong>
    <span>${result.verdict.because}</span>
    <span class="muted">${result.verdict.direction || "no direction"} ·
      your resume carries ${result.resume_shows} of your facts</span>`;

  $("job-title").textContent = result.job.title || "(title not read)";
  $("job-meta").textContent = [result.job.employer, result.job.location,
                               result.job.work_arrangement].filter(Boolean).join(" · ");

  $("classes").innerHTML = CLASS_VIEW.map(([key, heading, tone, advice]) => {
    const items = (result.classified || {})[key] || [];
    if (!items.length) return "";
    return `<section class="group ${tone}">
      <h3>${heading}</h3>
      <p class="advice">${advice}</p>
      <ul>${items.map((item) => `
        <li class="stack">
          <span class="name"><strong>${item.requirement}</strong>
            <span class="tag ${item.obligation === "required" ? "bad" : "warn"}">${item.obligation}</span></span>
          ${item.evidence.map((e) => `<span class="reasons">
            ${e.on_resume ? "on your resume" : "not on this resume"} ·
            ${e.quantified ? "has a figure" : "no figure"} — ${e.text}</span>`).join("")}
        </li>`).join("")}</ul>
    </section>`;
  }).join("") || "<p class='muted'>this posting stated no requirements we could read</p>";

  $("directions").innerHTML = result.directions.map((d) => {
    const reasons = [...(d.hard_failures || []), ...(d.review_reasons || [])].slice(0, 3).join(", ");
    return `<li><span class="tag ${d.decision === "match" ? "match" : d.decision === "fail" ? "bad" : "warn"}">${d.decision}</span>
      <span class="name">${d.name || d.direction_id}<span class="reasons">${reasons}</span></span>
      <span class="score">${d.ranking_score ?? ""}</span></li>`;
  }).join("");
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
  // The pane also holds the site's own headings — a feedback prompt, a section label — so
  // pick the first heading that reads like a job title rather than the first heading.
  const CHROME_HEADINGS = /^(are these results|about the job|people (also viewed|you can)|similar jobs|job search|meet the hiring|set alert|show more|premium)/i;
  const heading = [...pane.querySelectorAll("h1, h2")]
    .map((node) => (node.innerText || "").trim())
    .find((value) => value.length > 2 && value.length < 140
                     && !value.endsWith("?") && !CHROME_HEADINGS.test(value));
  // LinkedIn writes "Employer hiring Title in Location" into the document title.
  const fromDocument = (document.title || "").replace(/\s*\|\s*LinkedIn\s*$/i, "").trim();
  const documentMatch = fromDocument.match(/^(?<employer>.+?)\s+hiring\s+(?<title>.+?)\s+in\s+(?<location>.+)$/i);
  // The document title is the reliable source on LinkedIn; a heading inside the pane may
  // belong to any of the site's own widgets, and blocklisting them never finishes.
  const title = (documentMatch?.groups?.title || heading || fromDocument).trim();
  const employer = documentMatch?.groups?.employer?.trim() || "";
  // Not named `location`: that shadows window.location for the whole function.
  const place = documentMatch?.groups?.location?.trim() || "";
  // A search page keeps the result list and the open posting in one container. When the
  // title appears inside the text, everything before it is the list, so drop it.
  if ((title && matched === "main") || matched === "fallback_body") {
    const start = text.indexOf(title);
    if (title && start > 200) { text = text.slice(start); matched += "+sliced_at_title"; }
  }
  return { url: window.location.href.split("?")[0], text: text.slice(0, 60000), title,
           employer, location: place, container: matched };
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
