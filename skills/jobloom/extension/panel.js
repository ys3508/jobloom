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
      ? "本地服务已连接 · 已允许保存"
      : "本地服务已连接 · 只读";
  } catch {
    $("health").textContent = "本地服务未连接，请启动 Jobloom assist";
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
    ? "已允许读取岗位页面，可随时在 chrome://extensions 撤销"
    : "尚未授权；允许后才能读取当前岗位";
  $("grant").hidden = granted;
}

$("grant").addEventListener("click", async () => {
  // Must be called straight from the user's click for Chrome to show the dialog.
  try {
    await chrome.permissions.request({ origins: JOB_HOSTS });
  } catch (error) {
    $("access-state").textContent = "授权没有完成，请重试。";
  }
  await refreshAccess();
  readPosting();
});

const VERDICT_TEXT = {
  apply: ["值得投", "ok"],
  review: ["可以看看", "warn"],
  stretch: ["可以看看", "warn"],
  skip: ["不建议投", "bad"],
  unreadable: ["暂时读不了", "warn"]
};

// Four ways a requirement can stand, each asking for a different move. A keyword counter
// merges them and so rewards padding; keeping them apart is the point of this panel.
const CLASS_VIEW = [
  ["hidden_strength", "你做过、但简历没写", "ok", "补进简历，这是你已确认做过的事", false],
  ["real_gap", "你还没做过", "bad", "不要硬写；如实当作岗位挑战", false],
  ["evidence_gap", "简历写了，但证据偏薄", "warn", "还没写具体成果/数字，值得补强", false],
  ["transferable", "你有相邻经验", "warn", "按相邻经验表达，不说成直接做过", false],
  ["covered", "已经覆盖", "ok", "简历已有对应证据", true]
];

const HUMAN_ARRANGEMENT = { on_site: "现场", hybrid: "混合办公", remote: "远程" };

const REASON_TEXT = {
  outside_direction_title_scope: "岗位名称不在这个方向的范围内",
  auxiliary_title_without_direction_context: "岗位名称相关，但正文里的方向证据不足",
  target_title_without_direction_context: "岗位名称匹配，但正文里的方向证据不足",
  employer_sponsorship_history_investigation_required: "雇主的签证支持情况需要进一步确认",
  employer_sponsorship_conflict_requires_user_resolution: "雇主签证信息有冲突，需要你确认",
  required_sponsorship_not_supported: "岗位明确不支持所需签证",
  sponsorship_statement_non_visa_sense: "页面里的支持说明可能不是签证含义",
  seniority_outside_portfolio: "岗位级别超出当前方向范围",
  experience_requirement_above_candidate_range: "硬性年限要求高于当前经历范围",
  experience_preference_above_candidate_range: "偏好年限高于当前经历范围",
  direction_country_outside_scope: "岗位国家不在当前方向范围",
  job_card_unreviewed: "岗位要求尚未人工复核",
  hard_exclusion_context_review: "出现排除词，但需要结合上下文确认",
  direction_hard_exclusion: "岗位触发了这个方向的明确排除条件"
};

function evidenceLine(e) {
  const where = e.on_resume ? "简历已写" : "这份简历没写";
  const figure = e.quantified ? "已有具体成果/数字" : "还没写具体成果/数字";
  return `${where} · ${figure}`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
}

function render(result, page) {
  const call = result.verdict.call;
  const [label, cls] = VERDICT_TEXT[call] || ["需要确认", "warn"];
  const groups = result.classified || {};
  const count = (key) => (groups[key] || []).length;
  const hardGaps = (groups.real_gap || []).filter((g) => g.obligation === "required").length;
  const unassessed = result.unassessed_requirements || [];
  const statedRequirements = result.stated_requirements || [];
  const unreadable = call === "unreadable";

  $("verdict").className = `verdict ${cls}`;
  $("verdict").innerHTML = `<strong>${label}</strong>`;

  $("job-title").textContent = result.job.title || "（岗位名称未读到）";
  $("job-meta").textContent = [
    result.job.employer, result.job.location,
    HUMAN_ARRANGEMENT[result.job.work_arrangement]
  ].filter((value) => value && value !== "unknown").join(" · ");

  const opening = call === "apply" ? "你的经历跟这个岗位很搭。"
    : call === "skip" ? "这个岗位与你目前的证据匹配较弱。"
    : call === "stretch" ? "这个岗位有一些匹配，但整体偏挑战。"
    : call === "review" ? "这个岗位有匹配点，但还有信息值得确认。" : "";
  const moves = [];
  if (count("hidden_strength")) moves.push(`有 ${count("hidden_strength")} 项你做过、简历没写——补上更强`);
  if (hardGaps) moves.push(`${hardGaps} 项硬要求目前没有证据`);
  if (count("evidence_gap")) moves.push(`${count("evidence_gap")} 项还没写具体成果/数字`);
  $("why").textContent = unreadable
    ? "这个岗位页面的格式我暂时读不了，换成打开岗位详情页再试。"
    : `${opening}${moves.length ? moves.join("；") + "。" : "没有需要优先处理的硬伤。"}`;

  $("best-direction").hidden = unreadable || !result.verdict.direction;
  $("best-direction").textContent = result.verdict.direction
    ? `最匹配方向：${result.verdict.direction}` : "";
  $("actions").hidden = unreadable;
  const suggestedChoice = call === "skip" ? "skip"
    : call === "apply" && !count("hidden_strength") && !count("evidence_gap") ? "broad" : "precision";
  document.querySelectorAll("#actions button").forEach((button) => {
    button.classList.toggle("selected", button.dataset.choice === suggestedChoice);
    button.setAttribute("aria-pressed", String(button.dataset.choice === suggestedChoice));
  });

  $("classes").innerHTML = CLASS_VIEW.map(([key, heading, tone, advice, collapsed]) => {
    const items = groups[key] || [];
    if (!items.length) return "";
    const body = `<p class="advice">${advice}</p>
      <ul>${items.map((item) => {
        const first = item.evidence[0];
        const rest = item.evidence.slice(1);
        // One line and the move; the rest of the wording is there if it is wanted.
        return `<li class="stack">
          <span class="name"><strong>${escapeHtml(item.requirement)}</strong>
            <span class="tag ${item.obligation === "required" ? "bad" : "muted-tag"}">${
              item.obligation === "required" ? "硬要求" : "加分项"}</span></span>
          ${first ? `<span class="reasons">${evidenceLine(first)}</span>
            <details class="quote"><summary>查看你的证据</summary>
              <span class="reasons">${escapeHtml(first.text)}</span>
              ${rest.map((e) => `<span class="reasons">${escapeHtml(e.text)}</span>`).join("")}
            </details>` : ""}
        </li>`;
      }).join("")}</ul>`;
    return collapsed
      ? `<details class="group ${tone}"><summary>${heading} · ${items.length}</summary>${body}</details>`
      : `<section class="group ${tone}"><h3>${heading}</h3>${body}</section>`;
  }).join("") || "<p class='muted'>没有需要逐条展开的差距。</p>";

  $("unassessed").hidden = !unassessed.length;
  $("unassessed").innerHTML = unassessed.length ? `<h3>需要你确认 · ${unassessed.length}</h3>
    <p class="advice">这些是岗位原文要求，当前事实库还不能安全判断，不计为满足或缺失。</p>
    <ul>${unassessed.map((item) => `<li class="stack"><span class="name"><strong>${
      escapeHtml(item.requirement)}</strong><span class="tag ${
      item.obligation === "required" ? "bad" : "muted-tag"}">${
      item.obligation === "required" ? "硬要求" : "加分项"}</span></span></li>`).join("")}</ul>` : "";

  $("stated-requirements").hidden = !statedRequirements.length;
  $("stated-requirements").innerHTML = statedRequirements.length
    ? `<h3>岗位原文要求 · ${statedRequirements.length}</h3>
      <ul>${statedRequirements.map((item) => `<li class="stack"><span class="name"><strong>${
        escapeHtml(item.requirement)}</strong>${item.recognized_terms.length
          ? `<span class="reasons">对照了这些技能：${escapeHtml(item.recognized_terms.join("、"))}</span>`
          : `<span class="reasons">需要人工对照经历</span>`}</span></li>`).join("")}</ul>`
    : "";

  const otherDirections = (result.directions || []).filter((direction) =>
    direction.name && direction.name !== result.verdict.direction);
  $("directions").innerHTML = otherDirections.map((direction) => {
    const reasons = [...(direction.hard_failures || []), ...(direction.review_reasons || [])]
      .map((reason) => REASON_TEXT[reason]).filter(Boolean).slice(0, 2);
    return `<li><span class="name">${escapeHtml(direction.name)}${reasons.length
      ? `<span class="reasons">${reasons.join("；")}</span>` : ""}</span></li>`;
  }).join("");
  $("detail").hidden = !otherDirections.length;
  $("notice").textContent = unreadable
    ? "只读取当前页面；未保存任何内容。"
    : "仅基于当前页面生成草稿判断；未保存任何内容。";
  const diagnostics = page?.diagnostics;
  $("page-diagnostics").hidden = !unreadable;
  $("diagnostics").textContent = diagnostics
    ? (diagnostics.chars ? `读取到 ${diagnostics.chars} 个字符：${diagnostics.opening}`
      : "没有找到可用的岗位正文。") : "没有页面读取信息。";
  $("classes-drawer").hidden = false;
  $("classes-drawer").querySelector(":scope > summary").textContent = unreadable
    ? "页面读取详情" : "逐条看：你有什么、缺什么";
  $("stated-requirements").hidden = unreadable || !statedRequirements.length;
  $("unassessed").hidden = unreadable || !unassessed.length;
  $("classes").hidden = unreadable;
  $("detail").hidden = unreadable || !otherDirections.length;
  $("result").hidden = false;
}

document.querySelectorAll("#actions button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("#actions button").forEach((candidate) => {
      const selected = candidate === button;
      candidate.classList.toggle("selected", selected);
      candidate.setAttribute("aria-pressed", String(selected));
    });
  });
});

// Injected into the tab only when the user presses the button. Nothing from this
// extension runs on a job site before that press, and the injection reads the text the
// page has already rendered — it does not fetch, follow, or expand anything.
async function readVisiblePosting(previousPosting = {}) {
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
    const descriptions = found
      ? semanticDescriptions().filter((node) => found.pane.contains(node)) : [];
    const structuredDescription = descriptions
      .sort((a, b) => longerText(b).length - longerText(a).length)[0];
    const description = structuredDescription || (found && bigEnough(found.pane) ? found.pane : null);
    const usedFullBodyFallback = Boolean(description && !structuredDescription);
    const headerText = longerText(alignedHeader);
    let bodyText = longerText(description);
    if (usedFullBodyFallback && headerText) {
      const headerAt = bodyText.indexOf(headerText);
      if (headerAt >= 0 && headerAt < 300) bodyText = bodyText.slice(headerAt + headerText.length).trim();
    }
    const bodySignature = bodyText
      ? `${bodyText.length}:${bodyText.slice(0, 240)}:${bodyText.slice(-240)}` : "";
    const changedJob = Boolean(previousPosting.postingId
      && currentId !== previousPosting.postingId);
    const bodyChanged = !changedJob || bodySignature !== previousPosting.bodySignature;
    return { ...found, expectedTitle, heading,
      headerText, description, descriptions, bodyText, bodySignature, bodyChanged,
      usedFullBodyFallback,
      aligned: currentQueryId
        ? Boolean(alignedLink && description && bodyChanged) : true };
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
  tried.push(`body_changed:${reading.bodyChanged}`);
  if (!reading.aligned) {
    return { stale: true, postingId: currentId || "", url: window.location.href,
      diagnostics: { tried, chars: 0,
        opening: "detail header changed but its description did not reach the current job" } };
  }

  const { pane } = reading;
  let matched = reading.matched;
  const descriptions = reading.descriptions;
  const description = reading.description;
  let text = reading.bodyText || longerText(pane);
  if (reading.usedFullBodyFallback) matched += "+full_body";
  else if (description) matched += "+description";

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
    bodySignature: reading.bodySignature,
    // Enough for one screenshot to say what happened, instead of a console session.
    diagnostics: { tried: [...tried, `description_candidates:${descriptions.length}`],
      chars: text.length, opening: text.slice(0, 70).replace(/\n/g, " ") }
  };
}

// Following the user to the posting they just clicked is not the same as going somewhere
// they are not. The listener is scoped to the tab they are on, runs only while this panel
// is open, and reads nothing until the posting they are looking at actually changes.
let lastPostingKey = "";
let lastPostingSnapshot = { postingId: "", bodySignature: "" };
let readGeneration = 0;

async function readPosting({ onlyIfChanged = false } = {}) {
  if (!state.token || !(await hasPageAccess())) return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;
  $("status").textContent = onlyIfChanged
    ? "正在等待新岗位正文…" : "正在读取当前岗位…";
  const generation = ++readGeneration;
  try {
    const [injected] = await chrome.scripting.executeScript({
      target: { tabId: tab.id }, func: readVisiblePosting, args: [lastPostingSnapshot]
    });
    const page = injected?.result;
    if (page?.stale) throw new Error("posting_not_ready");
    if (!page?.text) throw new Error("posting_text_missing");
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
    $("status").textContent = "";
    render(body, page);
    lastPostingKey = key;
    lastPostingSnapshot = { postingId: page.postingId, bodySignature: page.bodySignature };
  } catch (error) {
    if (generation === readGeneration) {
      $("status").textContent = "这个岗位页面的格式我暂时读不了，换成打开岗位详情页再试。";
    }
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
