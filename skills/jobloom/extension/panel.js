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
  // Setup only asks for attention while something is missing.
  $("setup").open = !state.token;
  await checkHealth();
  // Opening this panel is the user asking, so read the posting they already have open
  // rather than making them press a second button for the same intent. Nothing else
  // triggers a read: no navigation hook, no polling, no reading of tabs they did not open.
  readPosting();
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
  await checkHealth();
  $("setup").open = false;
  readPosting();
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
}

$("grant").addEventListener("click", async () => {
  // Must be called straight from the user's click for Chrome to show the dialog.
  try {
    await chrome.permissions.request({ origins: JOB_HOSTS });
  } catch (error) {
    $("access-state").textContent = String(error.message || error);
  }
  await refreshAccess();
  readPosting();
});

const VERDICT_TEXT = {
  apply: ["Worth applying", "ok"],
  review: ["Worth a look", "warn"],
  stretch: ["A stretch", "warn"],
  skip: ["Probably skip", "bad"],
  // Not a judgement about the user: the page simply did not give up its requirements.
  unreadable: ["Could not read this posting", "warn"]
};

// Four ways a requirement can stand, each asking for a different move. A keyword counter
// merges them and so rewards padding; keeping them apart is the point of this panel.
const CLASS_VIEW = [
  ["hidden_strength", "You have done this — the resume does not show it", "ok",
   "add it; this is your own confirmed work", false],
  ["real_gap", "You have not done this", "bad",
   "leave it out; a stretch is honest, an invention is not", false],
  ["evidence_gap", "Shown, but thin", "warn",
   "no figure or outcome attached; worth strengthening", false],
  ["transferable", "Related, not the same thing", "warn",
   "say it as adjacent work — it never becomes direct experience", false],
  // Nothing to act on, so it does not get a screen of its own.
  ["covered", "Already covered and shown", "ok", "nothing to do", true]
];

const HUMAN_ARRANGEMENT = { on_site: "on site", hybrid: "hybrid", remote: "remote" };

function evidenceLine(e) {
  const where = e.on_resume ? "already on your resume" : "not on this resume";
  const figure = e.quantified ? "has a figure" : "no figure or outcome";
  return `${where} · ${figure}`;
}

function render(result) {
  const [label, cls] = VERDICT_TEXT[result.verdict.call] || ["Unclear", "warn"];
  const groups = result.classified || {};
  const count = (key) => (groups[key] || []).length;
  const hardGaps = (groups.real_gap || []).filter((g) => g.obligation === "required").length;

  $("verdict").className = `verdict ${cls}`;
  $("verdict").innerHTML = `<strong>${label}</strong>
    <span>${result.verdict.because}</span>
    <span class="tally">
      <b class="ok">${count("hidden_strength")}</b> to add ·
      <b class="bad">${hardGaps}</b> required gap${hardGaps === 1 ? "" : "s"} ·
      ${result.verdict.covered}/${result.verdict.stated} requirements met</span>`;

  $("job-title").textContent = result.job.title || "(title not read)";
  $("job-meta").textContent = [
    result.job.employer, result.job.location,
    HUMAN_ARRANGEMENT[result.job.work_arrangement]
  ].filter((value) => value && value !== "unknown").join(" · ");

  $("classes").innerHTML = CLASS_VIEW.map(([key, heading, tone, advice, collapsed]) => {
    const items = groups[key] || [];
    if (!items.length) return "";
    const body = `<p class="advice">${advice}</p>
      <ul>${items.map((item) => {
        const first = item.evidence[0];
        const rest = item.evidence.slice(1);
        // One line and the move; the rest of the wording is there if it is wanted.
        return `<li class="stack">
          <span class="name"><strong>${item.requirement}</strong>
            <span class="tag ${item.obligation === "required" ? "bad" : "muted-tag"}">${
              item.obligation === "required" ? "required" : "nice to have"}</span></span>
          ${first ? `<span class="reasons">${evidenceLine(first)}</span>
            <details class="quote"><summary>your words</summary>
              <span class="reasons">${first.text}</span>
              ${rest.map((e) => `<span class="reasons">${e.text}</span>`).join("")}
            </details>` : ""}
        </li>`;
      }).join("")}</ul>`;
    return collapsed
      ? `<details class="group ${tone}"><summary>${heading} · ${items.length}</summary>${body}</details>`
      : `<section class="group ${tone}"><h3>${heading}</h3>${body}</section>`;
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
  // Finding the open posting by class name or by URL parameter has failed repeatedly, so
  // this looks for what the user can see instead: the Apply control belongs to the posting
  // and never to the list beside it, so the smallest block containing it and a description
  // is the posting. Every strategy reports itself, so a wrong reading says which one ran.
  const tried = [];
  const bigEnough = (node) => node && (node.innerText || "").trim().length > 500;

  const climb = (node, label) => {
    let current = node;
    while (current && current !== document.body) {
      if (bigEnough(current)) return { pane: current, matched: label };
      current = current.parentElement;
    }
    return null;
  };

  let found = null;

  // 1. The Apply control the user would press for the posting they are reading.
  const controls = [...document.querySelectorAll("button, a")].filter((node) => {
    const label = (node.innerText || node.getAttribute("aria-label") || "").trim();
    return /^(apply|easy apply|save)\b/i.test(label);
  });
  tried.push(`apply_controls:${controls.length}`);
  for (const control of controls) {
    found = climb(control, "apply_control");
    if (found) break;
  }

  // 2. The link to the job the URL says is open.
  const currentId = new URLSearchParams(location.search).get("currentJobId")
    || (location.pathname.match(/\/jobs\/view\/(\d+)/) || [])[1];
  tried.push(`current_id:${currentId || "none"}`);
  if (!found && currentId) {
    found = climb(document.querySelector(`a[href*="/jobs/view/${currentId}"]`), "current_job_link");
  }

  // 3. Named containers, then the page itself.
  if (!found) {
    for (const selector of [".jobs-search__job-details--wrapper", ".jobs-details__main-content",
                            ".jobs-details", ".job-view-layout", "#jobsearch-ViewjobPaneWrapper",
                            ".jobsearch-JobComponent", "main"]) {
      const node = document.querySelector(selector);
      if (bigEnough(node)) { found = { pane: node, matched: selector }; break; }
    }
  }
  if (!found) found = { pane: document.body, matched: "fallback_body" };

  const { pane } = found;
  let matched = found.matched;
  let text = (pane.innerText || "").replace(/\n{3,}/g, "\n\n").trim();

  // The page's own name for this job: the link to it, or the first heading in the block.
  let heading = "";
  if (currentId) {
    const link = document.querySelector(`a[href*="/jobs/view/${currentId}"]`);
    heading = ((link?.closest("h1, h2, h3")?.innerText) || link?.innerText || "").trim().split("\n")[0];
  }
  if (!heading) {
    heading = [...pane.querySelectorAll("h1, h2")]
      .map((node) => (node.innerText || "").trim().split("\n")[0])
      .find((value) => value.length > 2 && value.length < 140 && !value.endsWith("?")) || "";
  }
  const fromDocument = (document.title || "").replace(/\s*\|\s*LinkedIn\s*$/i, "").trim();
  const documentMatch = fromDocument.match(/^(?<employer>.+?)\s+hiring\s+(?<title>.+?)\s+in\s+(?<location>.+)$/i);
  const title = (documentMatch?.groups?.title || heading || fromDocument).trim();
  const employer = documentMatch?.groups?.employer?.trim() || "";
  // Not named `location`: that shadows window.location for the whole function.
  const place = documentMatch?.groups?.location?.trim() || "";

  if (matched === "main" || matched === "fallback_body") {
    const start = title ? text.indexOf(title) : -1;
    if (start > 200) { text = text.slice(start); matched += "+sliced_at_title"; }
  }
  return {
    url: window.location.href, postingId: currentId || "", text: text.slice(0, 60000),
    title, employer, location: place, container: matched,
    // Enough for one screenshot to say what happened, instead of a console session.
    diagnostics: { tried, chars: text.length, opening: text.slice(0, 70).replace(/\n/g, " ") }
  };
}

// Following the user to the posting they just clicked is not the same as going somewhere
// they are not. The listener is scoped to the tab they are on, runs only while this panel
// is open, and reads nothing until the posting they are looking at actually changes.
let lastPostingKey = "";

async function readPosting({ onlyIfChanged = false } = {}) {
  if (!state.token || !(await hasPageAccess())) return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;
  if (!onlyIfChanged) { $("status").textContent = "reading the open posting…"; }
  try {
    const [injected] = await chrome.scripting.executeScript({
      target: { tabId: tab.id }, func: readVisiblePosting
    });
    const page = injected?.result;
    if (!page?.text) throw new Error("this page did not return a posting");
    const key = page.postingId || page.url;
    if (onlyIfChanged && key === lastPostingKey) return;
    lastPostingKey = key;
    const response = await fetch(`${state.endpoint}/positioning`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Jobloom-Token": state.token },
      body: JSON.stringify(page)
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `bridge returned ${response.status}`);
    $("status").textContent = page.container.startsWith("current_job_pane")
      ? "" : `read from ${page.container}`;
    render(body);
  } catch (error) {
    if (!onlyIfChanged) $("status").textContent = String(error.message || error);
  }
}

// LinkedIn swaps postings with history.pushState, which is a same-document navigation:
// tabs.onUpdated does not report it, so the panel never heard the user move. The event
// that does report it is onHistoryStateUpdated, filtered here to the two job sites and
// then to the tab the user is actually on.
const followUser = (details) => {
  if (details.frameId !== 0) return;              // top frame only
  chrome.tabs.query({ active: true, currentWindow: true }).then(([active]) => {
    if (active?.id !== details.tabId) return;     // never a tab the user is not on
    readPosting({ onlyIfChanged: true });
  });
};
const JOB_URL_FILTER = { url: [{ hostSuffix: "linkedin.com" }, { hostSuffix: "indeed.com" }] };
chrome.webNavigation.onHistoryStateUpdated.addListener(followUser, JOB_URL_FILTER);
chrome.webNavigation.onCompleted.addListener(followUser, JOB_URL_FILTER);

loadSettings();
refreshAccess();
