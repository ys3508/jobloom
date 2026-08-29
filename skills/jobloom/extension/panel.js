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

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
}

function render(result, page) {
  const [label, cls] = VERDICT_TEXT[result.verdict.call] || ["Unclear", "warn"];
  const groups = result.classified || {};
  const count = (key) => (groups[key] || []).length;
  const hardGaps = (groups.real_gap || []).filter((g) => g.obligation === "required").length;
  const unassessed = result.unassessed_requirements || [];
  const statedRequirements = result.stated_requirements || [];

  $("verdict").className = `verdict ${cls}`;
  $("verdict").innerHTML = `<strong>${label}</strong>
    <span>${result.verdict.because}</span>
    <span class="tally">
      <b class="ok">${count("hidden_strength")}</b> to add ·
      <b class="bad">${hardGaps}</b> required gap${hardGaps === 1 ? "" : "s"} ·
      ${result.verdict.covered}/${result.verdict.stated} recognized terms supported${
        statedRequirements.length ? ` · ${statedRequirements.length} JD line${
          statedRequirements.length === 1 ? "" : "s"} read` : ""}${
        unassessed.length ? ` · <b class="warn">${unassessed.length}</b> requirement line${
          unassessed.length === 1 ? "" : "s"} need review` : ""}</span>`;

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

  $("unassessed").hidden = !unassessed.length;
  $("unassessed").innerHTML = unassessed.length ? `<h3>Not automatically judged · ${unassessed.length}</h3>
    <p class="advice">These are requirements from the posting, but the current fact matcher
      cannot judge them safely. They are not counted as met or missing.</p>
    <ul>${unassessed.map((item) => `<li class="stack"><span class="name"><strong>${
      escapeHtml(item.requirement)}</strong><span class="tag ${
      item.obligation === "required" ? "bad" : "muted-tag"}">${
      item.obligation === "required" ? "required" : "nice to have"}</span></span></li>`).join("")}</ul>` : "";

  $("stated-requirements").hidden = !statedRequirements.length;
  $("stated-requirements").innerHTML = statedRequirements.length
    ? `<h3>Requirements read from the posting · ${statedRequirements.length}</h3>
      <ul>${statedRequirements.map((item) => `<li class="stack"><span class="name"><strong>${
        escapeHtml(item.requirement)}</strong>${item.recognized_terms.length
          ? `<span class="reasons">terms checked: ${escapeHtml(item.recognized_terms.join(", "))}</span>`
          : `<span class="reasons">needs a human evidence check</span>`}</span></li>`).join("")}</ul>`
    : "";

  $("directions").innerHTML = result.directions.map((d) => {
    const reasons = [...(d.hard_failures || []), ...(d.review_reasons || [])].slice(0, 3).join(", ");
    return `<li><span class="tag ${d.decision === "match" ? "match" : d.decision === "fail" ? "bad" : "warn"}">${d.decision}</span>
      <span class="name">${d.name || d.direction_id}<span class="reasons">${reasons}</span></span>
      <span class="score">${d.ranking_score ?? ""}</span></li>`;
  }).join("");
  $("notice").textContent = result.notice || "";
  const diagnostics = page?.diagnostics;
  $("page-diagnostics").hidden = result.verdict.call !== "unreadable";
  $("diagnostics").textContent = diagnostics
    ? `${diagnostics.tried.join(" · ")} · ${diagnostics.chars} chars · ${diagnostics.opening}`
    : "no page diagnostics returned";
  $("result").hidden = false;
}

// Injected into the tab only when the user presses the button. Nothing from this
// extension runs on a job site before that press, and the injection reads the text the
// page has already rendered — it does not fetch, follow, or expand anything.
async function readVisiblePosting() {
  // Finding the open posting by class name or by URL parameter has failed repeatedly, so
  // this looks for what the user can see instead: the Apply control belongs to the posting
  // and never to the list beside it, so the smallest block containing it and a description
  // is the posting. Every strategy reports itself, so a wrong reading says which one ran.
  const tried = [];
  const clean = (value) => (value || "").replace(/\u00a0/g, " ")
    .replace(/[ \t]{2,}/g, " ").replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n").trim();
  const contentText = (node) => {
    if (!node) return "";
    const blocks = new Set(["ADDRESS", "ARTICLE", "ASIDE", "BLOCKQUOTE", "BR", "DIV",
      "DL", "DT", "DD", "FIGCAPTION", "FOOTER", "H1", "H2", "H3", "H4", "H5",
      "H6", "HEADER", "HR", "LI", "MAIN", "NAV", "OL", "P", "PRE", "SECTION", "UL"]);
    const collect = (current) => {
      if (current.nodeType === Node.TEXT_NODE) return current.textContent || "";
      const body = [...current.childNodes].map(collect).join("");
      return blocks.has(current.nodeName) ? `\n${body}\n` : body;
    };
    return clean(collect(node));
  };
  const visibleText = (node) => clean(node?.innerText || "");
  const longerText = (node) => {
    const visible = visibleText(node);
    const complete = contentText(node);
    return complete.length > visible.length ? complete : visible;
  };
  const bigEnough = (node) => node && longerText(node).length > 500;

  const climb = (node, label) => {
    let current = node;
    while (current && current !== document.body) {
      if (bigEnough(current)) return { pane: current, matched: label };
      current = current.parentElement;
    }
    return null;
  };

  const currentQueryId = new URLSearchParams(location.search).get("currentJobId");
  const currentId = currentQueryId
    || (location.pathname.match(/\/jobs\/view\/(\d+)/) || [])[1];
  tried.push(`current_id:${currentId || "none"}`);
  const currentLinks = () => currentId
    ? [...document.querySelectorAll(`a[href*="/jobs/view/${currentId}"]`)] : [];
  const linkTitle = (link) => clean(
    link?.closest("h1, h2, h3")?.innerText || link?.innerText || "").split("\n")[0];
  const semanticDescriptions = () => {
    const named = [".jobs-description", "[class*='jobs-description']",
      "[id*='job-details']", "[aria-label*='job description' i]"]
      .flatMap((selector) => [...document.querySelectorAll(selector)]).filter(bigEnough);
    const headed = [...document.querySelectorAll("h1, h2, h3, h4, h5, h6")]
      .filter((node) => /^(about the job|job description)$/i.test(clean(node.innerText)))
      .map((node) => climb(node, "description_heading")?.pane).filter(Boolean);
    return [...new Set([...named, ...headed])];
  };

  const inspect = () => {
    let found = null;
    const controls = [...document.querySelectorAll("button, a")].filter((node) => {
      const label = clean(node.innerText || node.getAttribute("aria-label") || "");
      return /^(apply|easy apply|save)\b/i.test(label);
    });
    const links = currentLinks();
    let alignedLink = null;
    let alignedHeader = null;

    // LinkedIn now renders the title as a plain p > a, not a heading. The current job is
    // ready only when its current-id link and Apply control share the small detail header.
    // Before that happens their first common ancestor is the whole search layout.
    for (const control of controls) {
      let current = control;
      while (current && current !== document.body) {
        const link = links.find((candidate) => current.contains(candidate));
        if (link && longerText(current).length < 2000) {
          alignedLink = link;
          alignedHeader = current;
          found = climb(current, "current_job_with_apply");
          break;
        }
        current = current.parentElement;
      }
      if (alignedLink) break;
    }

    for (const description of semanticDescriptions()) {
      if (found) break;
      let current = description;
      while (current && current !== document.body) {
        if (controls.some((control) => current.contains(control))) {
          found = { pane: current, matched: "description_with_apply" };
          break;
        }
        current = current.parentElement;
      }
      if (found) break;
    }
    if (!found) {
      for (const control of controls) {
        found = climb(control, "apply_control");
        if (found) break;
      }
    }
    if (!found && currentId) {
      found = climb(document.querySelector(`a[href*="/jobs/view/${currentId}"]`), "current_job_link");
    }
    if (!found) {
      for (const selector of [".jobs-search__job-details--wrapper", ".jobs-details__main-content",
                              ".jobs-details", ".job-view-layout", "#jobsearch-ViewjobPaneWrapper",
                              ".jobsearch-JobComponent", "main"]) {
        const node = document.querySelector(selector);
        if (bigEnough(node)) { found = { pane: node, matched: selector }; break; }
      }
    }
    if (!found) found = { pane: document.body, matched: "fallback_body" };

    const expectedTitle = linkTitle(alignedLink || links[0]);
    const headings = [...found.pane.querySelectorAll("h1, h2")]
      .map((node) => clean(node.innerText).split("\n")[0])
      .filter((value) => value.length > 2 && value.length < 140 && !value.endsWith("?"));
    const heading = headings.find((value) => expectedTitle &&
      (value.includes(expectedTitle) || expectedTitle.includes(value))) || headings[0] || "";
    return { ...found, expectedTitle, heading,
      headerText: longerText(alignedHeader),
      aligned: currentQueryId ? Boolean(alignedLink) : true };
  };

  // History changes before LinkedIn finishes replacing the detail pane. Wait for paint,
  // and accept the pane only when its heading agrees with the link for currentJobId.
  let reading = inspect();
  let frames = 0;
  while (!reading.aligned && frames < 300) {
    await new Promise((resolve) => requestAnimationFrame(resolve));
    reading = inspect();
    frames += 1;
  }
  tried.push(`render_frames:${frames}`);
  tried.push(`aligned:${reading.aligned}`);
  if (!reading.aligned) {
    return { stale: true, postingId: currentId || "", url: window.location.href,
      diagnostics: { tried, chars: 0, opening: "detail pane did not reach the current job" } };
  }

  const { pane } = reading;
  let matched = reading.matched;
  const descriptions = semanticDescriptions().filter((node) => pane.contains(node));
  const description = descriptions.sort((a, b) => longerText(b).length - longerText(a).length)[0];
  let text = longerText(description || pane);
  if (description) matched += "+description";

  // The page's own name for this job comes first from the current-id link.
  const heading = reading.expectedTitle || reading.heading;
  const fromDocument = (document.title || "").replace(/\s*\|\s*LinkedIn\s*$/i, "").trim();
  const documentMatch = fromDocument.match(/^(?<employer>.+?)\s+hiring\s+(?<title>.+?)\s+in\s+(?<location>.+)$/i);
  const linkedInMatch = fromDocument.match(/^(?<title>.+?)\s*\|\s*(?<employer>[^|]+)$/);
  const title = (documentMatch?.groups?.title || heading || linkedInMatch?.groups?.title
    || fromDocument).trim();
  const employer = (documentMatch?.groups?.employer || linkedInMatch?.groups?.employer || "").trim();
  // Not named `location`: that shadows window.location for the whole function.
  const headerLines = clean(reading.headerText).split("\n").filter(Boolean);
  const titleLine = headerLines.findIndex((line) => line.includes(title) || title.includes(line));
  const place = (documentMatch?.groups?.location
    || (titleLine >= 0 ? headerLines[titleLine + 1]?.split(" · ")[0] : "") || "").trim();
  const headerLower = reading.headerText.toLowerCase();
  const workArrangement = /\bhybrid\b/.test(headerLower) ? "hybrid"
    : /\b(on-site|onsite|on site)\b/.test(headerLower) ? "on_site"
    : /\bremote\b/.test(headerLower) ? "remote" : "";

  if (matched === "main" || matched === "fallback_body") {
    const start = title ? text.indexOf(title) : -1;
    if (start > 200) { text = text.slice(start); matched += "+sliced_at_title"; }
  }
  return {
    url: window.location.href, postingId: currentId || "", text: text.slice(0, 60000),
    title, employer, location: place, work_arrangement: workArrangement, container: matched,
    // Enough for one screenshot to say what happened, instead of a console session.
    diagnostics: { tried: [...tried, `description_candidates:${descriptions.length}`],
      chars: text.length, opening: text.slice(0, 70).replace(/\n/g, " ") }
  };
}

// Following the user to the posting they just clicked is not the same as going somewhere
// they are not. The listener is scoped to the tab they are on, runs only while this panel
// is open, and reads nothing until the posting they are looking at actually changes.
let lastPostingKey = "";
let readGeneration = 0;

async function readPosting({ onlyIfChanged = false } = {}) {
  if (!state.token || !(await hasPageAccess())) return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;
  $("status").textContent = onlyIfChanged
    ? "waiting for the selected posting…" : "reading the open posting…";
  const generation = ++readGeneration;
  try {
    const [injected] = await chrome.scripting.executeScript({
      target: { tabId: tab.id }, func: readVisiblePosting
    });
    const page = injected?.result;
    if (page?.stale) throw new Error("the new posting is still rendering; choose it again");
    if (!page?.text) throw new Error("this page did not return a posting");
    const key = page.postingId || page.url;
    if (onlyIfChanged && key === lastPostingKey) {
      $("status").textContent = "";
      return;
    }
    const response = await fetch(`${state.endpoint}/positioning`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Jobloom-Token": state.token },
      body: JSON.stringify(page)
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `bridge returned ${response.status}`);
    if (generation !== readGeneration) return;
    $("status").textContent = `read from ${page.container}`;
    render(body, page);
    lastPostingKey = key;
  } catch (error) {
    if (generation === readGeneration) $("status").textContent = String(error.message || error);
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
