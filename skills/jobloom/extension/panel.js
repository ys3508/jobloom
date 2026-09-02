// The panel asks the content script for what is on screen, sends it to the local bridge,
// and renders the answer. It has no other outbound call: nothing here contacts a job site.

const $ = (id) => document.getElementById(id);
const state = { endpoint: "http://127.0.0.1:8787", token: "", language: "en" };

const I18N = {
  en: {
    language: "Language", scope: "Reads only the job you have open. It does not browse, apply, or submit.",
    connectionSettings: "Connection settings", localService: "Local service", accessToken: "Access token",
    save: "Save", grant: "Allow access to job pages", connectedReadOnly: "Local service connected · read only",
    connectedStore: "Local service connected · storing enabled", disconnected: "Local service is not connected",
    factStoreEmpty: "Experience library is empty — import your resume or experience before judging jobs",
    accessGranted: "Job-page access granted — revoke any time in chrome://extensions",
    accessMissing: "Page access is required to read the open job", accessFailed: "Permission was not granted. Try again.",
    reading: "Reading the open job…", waiting: "Waiting for the new job description…",
    apply: "Worth applying", review: "Worth a look", stretch: "Worth a look", skipVerdict: "Not a fit",
    unreadable: "Can't read this yet", partial: "Read incomplete", evidenceUnavailable: "Can't judge yet", unknownVerdict: "Needs review",
    noTitle: "Job title not found", openingApply: "Your background fits this job well. ",
    openingSkip: "This job is a weak match for your current evidence. ",
    openingStretch: "Some parts fit, but this role is a stretch. ",
    openingReview: "There is a match here, with a few things to confirm. ",
    hiddenMove: "{n} things you've done are missing from your resume — add them",
    hardGap: "{n} required items are not in your evidence", thinMove: "{n} items need a concrete result or number",
    noPriorityGap: "No priority gaps found.", unreadableMessage: "I can't read this page format yet. Open the full job details and try again.",
    partialMessage: "I only received part of this job description, so I did not judge it. Open the full job details and try again.",
    evidenceUnavailableMessage: "Your experience library is empty or unavailable, so I did not judge this job. Import your resume or experience and try again.",
    bestDirection: "Best-matching direction: {name}", tailorApply: "Tailor & apply", tailorApplySub: "Edit resume first",
    applyAsIs: "Apply as-is", applyAsIsSub: "Apply directly",
    saveLater: "Save for later", saveLaterSub: "Not now — keep it",
    confirmSubmitted: "I submitted it", confirmSubmittedSub: "Press after the form is sent",
    confirmedSubmitted: "Recorded as submitted — your word, not observed",
    alreadyConfirmed: "Already recorded as submitted", confirmFailed: "Could not record it",
    saved: "Saved to your tracker", loggedApplied: "Logged as applied — yours to confirm, not observed",
    saveFailed: "Could not save — is storing enabled?",
    storedNotice: "Kept in your local tracker. Nothing else was stored.",
    drawer: "See details: what you have and what is missing", otherDirections: "Other directions considered",
    readingDetails: "Page reading details", hiddenTitle: "You've done this — not on your resume",
    hiddenAdvice: "Add it to your resume; this is your confirmed work", gapTitle: "Not in your background",
    gapAdvice: "Do not claim it; treat it as a real job challenge", thinTitle: "On your resume, but thin",
    thinAdvice: "Add a concrete result or number", transferTitle: "Adjacent experience",
    transferAdvice: "Describe it as adjacent experience, not direct experience", coveredTitle: "Covered",
    coveredAdvice: "Your resume already shows supporting evidence", required: "required", preferred: "preferred",
    resumeShows: "on your resume", resumeMissing: "not on this resume", quantified: "has a concrete result or number",
    unquantified: "no concrete result or number yet", viewEvidence: "View your evidence", emptyDetails: "No itemized gaps.",
    confirmTitle: "Needs your review · {n}", confirmAdvice: "These are original job requirements the fact library cannot safely judge; they are not counted as met or missing.",
    statedTitle: "Original job requirements · {n}", comparedSkills: "Compared these skills: {skills}", manualCompare: "Needs a manual evidence check",
    experienceMatched: "Matched to your experience", experienceMissing: "No supporting experience found",
    notice: "Draft based only on the current page; nothing was stored.", unreadableNotice: "Only the current page was read; nothing was stored.",
    charsRead: "Read {n} characters: {opening}", noBody: "No usable job description was found.", noDiagnostics: "No page-reading diagnostics.",
    reasonOutside: "The title is outside this direction", reasonContext: "The title is related, but the job description lacks enough direction evidence",
    reasonSponsorCheck: "Employer sponsorship needs confirmation", reasonSponsorConflict: "Sponsorship information conflicts and needs confirmation",
    reasonSponsorNo: "The job explicitly does not support the required visa", reasonSponsorSense: "The word support may not refer to visas",
    reasonSeniority: "The job level is outside this direction", reasonYears: "Required experience exceeds the current range",
    reasonPreferredYears: "Preferred experience exceeds the current range", reasonCountry: "The job country is outside this direction",
    reasonUnreviewed: "The job requirements have not been reviewed", reasonExclusionReview: "An exclusion term needs context",
    reasonExclusion: "The job triggers an explicit exclusion for this direction",
    fillTitle: "Fill one page of an application form",
    fillSeparateWindow: "Runs one page in a separate guarded Jobloom browser window.",
    fillNoTabChange: "Your current tab will not be changed.",
    fillStopsBeforeSubmit: "Stops before Submit.",
    fillApplication: "Application ID", fillPrepare: "Prepare one page",
    fillRun: "Run this page", fillPreparing: "Preparing…", fillRunning: "Running one page…",
    fillPrepared: "Prepared. Review below, then run this one page.",
    fillDone: "Finished one page. Nothing was submitted.",
    fillNeedsApplication: "Enter the application ID to prepare a page",
    fillFailed: "Could not prepare or run: {code}",
    fillRowApplication: "Application", fillRowPage: "Page", fillRowActions: "Actions",
    fillRowControls: "Control kinds", fillRowSources: "Answer sources",
    fillRowOperations: "Operations", fillRowRisks: "Risks", fillRowExpires: "Prepared until",
    fillRowVerified: "Verified", fillRowPaused: "Paused", fillRowPending: "Not filled",
    fillRowReasons: "Reason codes", fillRowConsumed: "Package consumed",
    fillRowBoundary: "Submit control", fillRowSubmitBoundary: "observed, never acted on",
    fillNone: "none", fillYes: "yes", fillNo: "no", fillPageIndex: "page {n}",
    fillFinalPage: "final page", fillNotFinalPage: "not the final page"
  },
  zh: {
    language: "语言", scope: "只读取你当前打开的岗位，不浏览、不申请、不提交。", connectionSettings: "连接设置",
    localService: "本地服务", accessToken: "访问令牌", save: "保存", grant: "允许读取岗位页面",
    connectedReadOnly: "本地服务已连接 · 只读", connectedStore: "本地服务已连接 · 已允许保存", disconnected: "本地服务未连接",
    factStoreEmpty: "经历库为空，请先导入简历或经历，再判断岗位",
    accessGranted: "已允许读取岗位页面，可随时在 chrome://extensions 撤销", accessMissing: "需要授权后才能读取当前岗位", accessFailed: "授权没有完成，请重试。",
    reading: "正在读取当前岗位…", waiting: "正在等待新岗位正文…", apply: "值得投", review: "可以看看", stretch: "可以看看",
    skipVerdict: "不建议投", unreadable: "暂时读不了", partial: "读取不完整", evidenceUnavailable: "暂时判不了", unknownVerdict: "需要确认", noTitle: "岗位名称未读到",
    openingApply: "你的经历跟这个岗位很搭。", openingSkip: "这个岗位与你目前的证据匹配较弱。", openingStretch: "这个岗位有一些匹配，但整体偏挑战。",
    openingReview: "这个岗位有匹配点，但还有信息值得确认。", hiddenMove: "有 {n} 项你做过、简历没写——补上更强", hardGap: "{n} 项硬要求目前没有证据",
    thinMove: "{n} 项还没写具体成果/数字", noPriorityGap: "没有需要优先处理的硬伤。", unreadableMessage: "这个岗位页面的格式我暂时读不了，换成打开岗位详情页再试。",
    partialMessage: "我只读到了这个岗位的一部分，所以没有作出判断。请打开完整岗位详情页再试。", evidenceUnavailableMessage: "经历库为空或暂时不可用，所以没有判断这个岗位。请先导入简历或经历再试。", bestDirection: "最匹配方向：{name}",
    tailorApply: "精投", tailorApplySub: "改简历再投", applyAsIs: "广投", applyAsIsSub: "直接投",
    saveLater: "先存着", saveLaterSub: "现在不投，留着",
    confirmSubmitted: "投完了", confirmSubmittedSub: "表单真的提交后再按",
    confirmedSubmitted: "已记为投完 —— 你的声明，非系统观测",
    alreadyConfirmed: "已经记过了", confirmFailed: "没记上",
    saved: "已存入你的记录表", loggedApplied: "已记为已投——这是你的声明，系统没有核实",
    saveFailed: "没能存上——本地服务开了写入吗？",
    storedNotice: "已存入本地记录表。除此之外没有保存任何内容。",
    drawer: "逐条看：你有什么、缺什么", otherDirections: "其他考虑过的方向", readingDetails: "页面读取详情",
    hiddenTitle: "你做过、但简历没写", hiddenAdvice: "补进简历，这是你已确认做过的事", gapTitle: "你还没做过", gapAdvice: "不要硬写；如实当作岗位挑战",
    thinTitle: "简历写了，但证据偏薄", thinAdvice: "还没写具体成果/数字，值得补强", transferTitle: "你有相邻经验", transferAdvice: "按相邻经验表达，不说成直接做过",
    coveredTitle: "已经覆盖", coveredAdvice: "简历已有对应证据", required: "硬要求", preferred: "加分项", resumeShows: "简历已写", resumeMissing: "这份简历没写",
    quantified: "已有具体成果/数字", unquantified: "还没写具体成果/数字", viewEvidence: "查看你的证据", emptyDetails: "没有需要逐条展开的差距。",
    confirmTitle: "需要你确认 · {n}", confirmAdvice: "这些是岗位原文要求，当前事实库还不能安全判断，不计为满足或缺失。", statedTitle: "岗位原文要求 · {n}",
    comparedSkills: "对照了这些技能：{skills}", manualCompare: "需要人工对照经历", experienceMatched: "已在经历库找到对应证据", experienceMissing: "经历库未找到对应证据", notice: "仅基于当前页面生成草稿判断；未保存任何内容。",
    unreadableNotice: "只读取当前页面；未保存任何内容。", charsRead: "读取到 {n} 个字符：{opening}", noBody: "没有找到可用的岗位正文。", noDiagnostics: "没有页面读取信息。",
    reasonOutside: "岗位名称不在这个方向的范围内", reasonContext: "岗位名称相关，但正文里的方向证据不足", reasonSponsorCheck: "雇主的签证支持情况需要进一步确认",
    reasonSponsorConflict: "雇主签证信息有冲突，需要你确认", reasonSponsorNo: "岗位明确不支持所需签证", reasonSponsorSense: "页面里的支持说明可能不是签证含义",
    reasonSeniority: "岗位级别超出当前方向范围", reasonYears: "硬性年限要求高于当前经历范围", reasonPreferredYears: "偏好年限高于当前经历范围",
    reasonCountry: "岗位国家不在当前方向范围", reasonUnreviewed: "岗位要求尚未人工复核", reasonExclusionReview: "出现排除词，但需要结合上下文确认", reasonExclusion: "岗位触发了这个方向的明确排除条件",
    fillTitle: "填写申请表单的一页",
    fillSeparateWindow: "在另一个受保护的 Jobloom 浏览器窗口中运行一页。",
    fillNoTabChange: "不会改动你当前的标签页。",
    fillStopsBeforeSubmit: "在 Submit 之前停止。",
    fillApplication: "申请 ID", fillPrepare: "准备一页",
    fillRun: "运行这一页", fillPreparing: "正在准备…", fillRunning: "正在运行一页…",
    fillPrepared: "已准备好。先看下面，再运行这一页。",
    fillDone: "这一页已完成。没有提交任何东西。",
    fillNeedsApplication: "请填入申请 ID 才能准备一页",
    fillFailed: "无法准备或运行：{code}",
    fillRowApplication: "申请", fillRowPage: "页面", fillRowActions: "动作数",
    fillRowControls: "控件类型", fillRowSources: "答案来源",
    fillRowOperations: "操作", fillRowRisks: "风险", fillRowExpires: "准备状态有效至",
    fillRowVerified: "已核验", fillRowPaused: "已暂停", fillRowPending: "未填写",
    fillRowReasons: "原因代码", fillRowConsumed: "package 已消费",
    fillRowBoundary: "Submit 控件", fillRowSubmitBoundary: "只观察，从未操作",
    fillNone: "无", fillYes: "是", fillNo: "否", fillPageIndex: "第 {n} 页",
    fillFinalPage: "最后一页", fillNotFinalPage: "不是最后一页"
  }
};
const t = (key, values = {}) => Object.entries(values).reduce(
  (text, [name, value]) => text.replaceAll(`{${name}}`, value), I18N[state.language][key] || "");

function applyLanguage() {
  document.documentElement.lang = state.language === "zh" ? "zh-CN" : "en";
  document.querySelectorAll("[data-i18n]").forEach((node) => { node.textContent = t(node.dataset.i18n); });
  $("language").value = state.language;
}

async function loadSettings() {
  const saved = await chrome.storage.local.get(["endpoint", "token", "language"]);
  if (saved.endpoint) state.endpoint = saved.endpoint;
  if (saved.token) state.token = saved.token;
  if (saved.language === "zh" || saved.language === "en") state.language = saved.language;
  applyLanguage();
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
    $("health").textContent = !body.fact_store_ready ? t("factStoreEmpty") : body.store_enabled
      ? t("connectedStore") : t("connectedReadOnly");
  } catch {
    $("health").textContent = t("disconnected");
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

$("language").addEventListener("change", async () => {
  state.language = $("language").value === "zh" ? "zh" : "en";
  await chrome.storage.local.set({ language: state.language });
  applyLanguage();
  if (!$("result").hidden && state.lastResult) render(state.lastResult, state.lastPage);
  await checkHealth();
  await refreshAccess();
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
    ? t("accessGranted") : t("accessMissing");
  $("grant").hidden = granted;
}

$("grant").addEventListener("click", async () => {
  // Must be called straight from the user's click for Chrome to show the dialog.
  try {
    await chrome.permissions.request({ origins: JOB_HOSTS });
  } catch (error) {
    $("access-state").textContent = t("accessFailed");
  }
  await refreshAccess();
  readPosting();
});

const VERDICT_TEXT = {
  apply: ["apply", "ok"], review: ["review", "warn"], stretch: ["stretch", "warn"],
  skip: ["skipVerdict", "bad"], unreadable: ["unreadable", "warn"], partial: ["partial", "warn"],
  evidence_unavailable: ["evidenceUnavailable", "warn"]
};

// Four ways a requirement can stand, each asking for a different move. A keyword counter
// merges them and so rewards padding; keeping them apart is the point of this panel.
const CLASS_VIEW = [
  ["hidden_strength", "hiddenTitle", "ok", "hiddenAdvice", false],
  ["real_gap", "gapTitle", "bad", "gapAdvice", false],
  ["evidence_gap", "thinTitle", "warn", "thinAdvice", false],
  ["transferable", "transferTitle", "warn", "transferAdvice", false],
  ["covered", "coveredTitle", "ok", "coveredAdvice", true]
];

const HUMAN_ARRANGEMENT = {
  en: { on_site: "on-site", hybrid: "hybrid", remote: "remote" },
  zh: { on_site: "现场", hybrid: "混合办公", remote: "远程" }
};

const REASON_TEXT = {
  outside_direction_title_scope: "reasonOutside",
  auxiliary_title_without_direction_context: "reasonContext", target_title_without_direction_context: "reasonContext",
  employer_sponsorship_history_investigation_required: "reasonSponsorCheck",
  employer_sponsorship_conflict_requires_user_resolution: "reasonSponsorConflict",
  required_sponsorship_not_supported: "reasonSponsorNo", sponsorship_statement_non_visa_sense: "reasonSponsorSense",
  seniority_outside_portfolio: "reasonSeniority", experience_requirement_above_candidate_range: "reasonYears",
  experience_preference_above_candidate_range: "reasonPreferredYears", direction_country_outside_scope: "reasonCountry",
  job_card_unreviewed: "reasonUnreviewed", hard_exclusion_context_review: "reasonExclusionReview",
  direction_hard_exclusion: "reasonExclusion"
};

function evidenceLine(e) {
  const where = t(e.on_resume ? "resumeShows" : "resumeMissing");
  const figure = t(e.quantified ? "quantified" : "unquantified");
  return `${where} · ${figure}`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
}

function render(result, page) {
  const call = result.verdict.call;
  const [labelKey, cls] = VERDICT_TEXT[call] || ["unknownVerdict", "warn"];
  const label = t(labelKey);
  const groups = result.classified || {};
  const count = (key) => (groups[key] || []).length;
  const hardGaps = (groups.real_gap || []).filter((g) => g.obligation === "required").length;
  const unassessed = result.unassessed_requirements || [];
  const statedRequirements = result.stated_requirements || [];
  const unreadable = call === "unreadable";
  const partial = call === "partial";
  const evidenceUnavailable = call === "evidence_unavailable";
  const unavailable = unreadable || partial || evidenceUnavailable;

  $("verdict").className = `verdict ${cls}`;
  $("verdict").innerHTML = `<strong>${label}</strong>`;

  $("job-title").textContent = result.job.title || t("noTitle");
  $("job-meta").textContent = [
    result.job.employer, result.job.location,
    HUMAN_ARRANGEMENT[state.language][result.job.work_arrangement]
  ].filter((value) => value && value !== "unknown").join(" · ");

  const opening = call === "apply" ? t("openingApply") : call === "skip" ? t("openingSkip")
    : call === "stretch" ? t("openingStretch") : call === "review" ? t("openingReview") : "";
  const moves = [];
  if (count("hidden_strength")) moves.push(t("hiddenMove", { n: count("hidden_strength") }));
  if (hardGaps) moves.push(t("hardGap", { n: hardGaps }));
  if (count("evidence_gap")) moves.push(t("thinMove", { n: count("evidence_gap") }));
  $("why").textContent = unreadable ? t("unreadableMessage") : partial ? t("partialMessage")
    : evidenceUnavailable ? t("evidenceUnavailableMessage")
    : `${opening}${moves.length ? moves.join(state.language === "zh" ? "；" : "; ") + (state.language === "zh" ? "。" : ".") : t("noPriorityGap")}`;

  $("best-direction").hidden = unavailable || !result.verdict.direction;
  $("best-direction").textContent = result.verdict.direction
    ? t("bestDirection", { name: result.verdict.direction }) : "";
  $("actions").hidden = unavailable;
  // A posting judged not a fit gets no suggestion at all. Recommending "save for later" for
  // something the evidence says to walk away from would be a nudge the judgement does not
  // support, and moving on needs no button.
  const suggestedChoice = call === "skip" ? ""
    : call === "apply" && !count("hidden_strength") && !count("evidence_gap") ? "broad" : "precision";
  lastSuggestedChoice = suggestedChoice;
  $("confirm-row").hidden = true;
  document.querySelectorAll("#actions button").forEach((button) => {
    button.classList.toggle("selected", button.dataset.choice === suggestedChoice);
    button.setAttribute("aria-pressed", String(button.dataset.choice === suggestedChoice));
  });

  $("classes").innerHTML = CLASS_VIEW.map(([key, headingKey, tone, adviceKey, collapsed]) => {
    const items = groups[key] || [];
    if (!items.length) return "";
    const body = `<p class="advice">${t(adviceKey)}</p>
      <ul>${items.map((item) => {
        const first = item.evidence[0];
        const rest = item.evidence.slice(1);
        // One line and the move; the rest of the wording is there if it is wanted.
        return `<li class="stack">
          <span class="name"><strong>${escapeHtml(item.requirement)}</strong>
            <span class="tag ${item.obligation === "required" ? "bad" : "muted-tag"}">${
              item.obligation === "required" ? t("required") : t("preferred")}</span></span>
          ${first ? `<span class="reasons">${evidenceLine(first)}</span>
            <details class="quote"><summary>${t("viewEvidence")}</summary>
              <span class="reasons">${escapeHtml(first.text)}</span>
              ${rest.map((e) => `<span class="reasons">${escapeHtml(e.text)}</span>`).join("")}
            </details>` : ""}
        </li>`;
      }).join("")}</ul>`;
    return collapsed
      ? `<details class="group ${tone}"><summary>${t(headingKey)} · ${items.length}</summary>${body}</details>`
      : `<section class="group ${tone}"><h3>${t(headingKey)}</h3>${body}</section>`;
  }).join("") || `<p class='muted'>${t("emptyDetails")}</p>`;

  $("unassessed").hidden = !unassessed.length;
  $("unassessed").innerHTML = unassessed.length ? `<h3>${t("confirmTitle", { n: unassessed.length })}</h3>
    <p class="advice">${t("confirmAdvice")}</p>
    <ul>${unassessed.map((item) => `<li class="stack"><span class="name"><strong>${
      escapeHtml(item.requirement)}</strong><span class="tag ${
      item.obligation === "required" ? "bad" : "muted-tag"}">${
      item.obligation === "required" ? t("required") : t("preferred")}</span></span></li>`).join("")}</ul>` : "";

  $("stated-requirements").hidden = !statedRequirements.length;
  $("stated-requirements").innerHTML = statedRequirements.length
    ? `<h3>${t("statedTitle", { n: statedRequirements.length })}</h3>
      <ul>${statedRequirements.map((item) => `<li class="stack"><span class="name"><strong>${
        escapeHtml(item.requirement)}</strong>${item.recognized_terms.length
          ? `<span class="reasons">${t("comparedSkills", { skills: escapeHtml(item.recognized_terms.join(state.language === "zh" ? "、" : ", ")) })}</span>`
          : `<span class="reasons">${t(item.evidence_status === "matched" ? "experienceMatched"
            : item.evidence_status === "missing" ? "experienceMissing" : "manualCompare")}</span>`}</span></li>`).join("")}</ul>`
    : "";

  const otherDirections = (result.directions || []).filter((direction) =>
    direction.name && direction.name !== result.verdict.direction);
  $("directions").innerHTML = otherDirections.map((direction) => {
    const reasons = [...(direction.hard_failures || []), ...(direction.review_reasons || [])]
      .map((reason) => REASON_TEXT[reason]).filter(Boolean).map(t).slice(0, 2);
    return `<li><span class="name">${escapeHtml(direction.name)}${reasons.length
      ? `<span class="reasons">${reasons.join(state.language === "zh" ? "；" : "; ")}</span>` : ""}</span></li>`;
  }).join("");
  $("detail").hidden = !otherDirections.length;
  $("notice").textContent = unavailable ? t("unreadableNotice") : t("notice");
  const diagnostics = page?.diagnostics;
  $("page-diagnostics").hidden = !unavailable;
  // The trail was collected and then never shown, so a failed read could only report how
  // much text it got — not which strategy produced it. One screenshot should say what ran.
  $("diagnostics").textContent = diagnostics
    ? [(diagnostics.chars ? t("charsRead", { n: diagnostics.chars, opening: diagnostics.opening })
        : t("noBody")),
       (diagnostics.tried || []).join(" · ")].filter(Boolean).join("\n")
    : t("noDiagnostics");
  $("classes-drawer").hidden = false;
  $("classes-drawer").querySelector(":scope > summary").textContent = unavailable
    ? t("readingDetails") : t("drawer");
  $("stated-requirements").hidden = unavailable || !statedRequirements.length;
  $("unassessed").hidden = unavailable || !unassessed.length;
  $("classes").hidden = unavailable;
  $("detail").hidden = unavailable || !otherDirections.length;
  $("result").hidden = false;
  state.lastResult = result;
  state.lastPage = page;
}

let lastReading = null;

// All three record a decision; there is still no button meaning "do not apply", because
// skipping a job means moving to the next one and nobody would press it. What the two apply
// buttons record is an intention the user then carries out on the job site themselves —
// nothing here applies on their behalf, and the press lands before the form is even open.
// That is why finishing has its own, later button rather than being assumed from this one.
let lastSuggestedChoice = "";

async function recordDecision(decision) {
  if (!lastReading?.job || !lastReading?.page?.url) return;
  const job = lastReading.job;
  const seen = lastReading.body || {};
  const bucket = (name) => (seen.classified?.[name] || []).length;
  const judgement = {
    verdict: seen.verdict?.call || "",
    verdict_reason: seen.verdict?.because || "",
    direction: seen.verdict?.direction || "",
    covered: seen.verdict?.covered ?? null,
    stated: seen.verdict?.stated ?? null,
    hidden_strength: bucket("hidden_strength"),
    evidence_gap: bucket("evidence_gap"),
    suggested_choice: lastSuggestedChoice,
  };
  const response = await fetch(`${state.endpoint}/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Jobloom-Token": state.token },
    body: JSON.stringify({
      actor: "user",
      decision,
      judgement,
      job_card: {
        canonical_url: lastReading.page.url, title: job.title, employer: job.employer,
        location: job.location, country: job.country,
        work_arrangement: job.work_arrangement, employment_type: job.employment_type,
        source: "panel", ats: job.ats || "",
        extraction: { ats: { posted_at: job.posted_at || null, deadline: job.deadline || null,
                             apply_url: job.apply_url || null } },
      },
    }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `bridge returned ${response.status}`);
  return body;
}

// A second, later press than the one above: that one recorded a decision made before the
// form was open, and this one answers whether the form was finished. Folding them into one
// button would put the answer before the question.
$("confirm-submitted").addEventListener("click", async () => {
  if (!lastReading?.page?.url) return;
  try {
    const response = await fetch(`${state.endpoint}/confirm-submitted`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Jobloom-Token": state.token },
      body: JSON.stringify({ job_url: lastReading.page.url }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `bridge returned ${response.status}`);
    $("status").textContent = t(body.already_confirmed ? "alreadyConfirmed" : "confirmedSubmitted");
  } catch (error) {
    $("status").textContent = t("confirmFailed");
  }
});

document.querySelectorAll("#actions button").forEach((button) => {
  button.addEventListener("click", async () => {
    document.querySelectorAll("#actions button").forEach((candidate) => {
      const selected = candidate === button;
      candidate.classList.toggle("selected", selected);
      candidate.setAttribute("aria-pressed", String(selected));
    });
    // Both apply buttons record that the user is applying, because they will do it on the
    // job site and nothing here can watch them. It is their assertion, filed as theirs —
    // never the `submitted` state, which needs a confirmation page behind it.
    const decision = button.dataset.choice === "later" ? "later" : "applied";
    try {
      await recordDecision(decision);
      $("status").textContent = t(decision === "later" ? "saved" : "loggedApplied");
      // The panel promises nothing is stored. Once something is, it has to say so.
      $("notice").textContent = t("storedNotice");
      // Offered only once there is a decision to confirm, and only for applying: it asks
      // "did you finish the form", which is not a question about a job merely kept. The
      // press above happened before the form was open, so this is a second, later act —
      // it is deliberately not folded into the same button.
      $("confirm-row").hidden = decision === "later";
    } catch (error) {
      $("status").textContent = t("saveFailed");
    }
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
  // The detail pane holds one posting. The results rail holds many, and it is comfortably
  // longer than 500 characters, so a length test alone will happily return the rail — which
  // is how a Tempus posting came back carrying the text of a Boston University one that had
  // been open earlier. Counting `/jobs/view/` links alone was not enough: the rail does not
  // always link that way. Posting identity is collected from every attribute LinkedIn uses
  // for it, so the count survives whichever one a given layout happens to render.
  const postingIds = (node) => {
    const ids = new Set();
    if (!node || !node.querySelectorAll) return ids;
    for (const marked of node.querySelectorAll("[data-occludable-job-id], [data-job-id]")) {
      const id = marked.getAttribute("data-occludable-job-id") || marked.getAttribute("data-job-id");
      if (id) ids.add(id);
    }
    for (const link of node.querySelectorAll('a[href*="/jobs/view/"], a[href*="currentJobId="]')) {
      const href = link.getAttribute("href") || "";
      const match = href.match(/\/jobs\/view\/(\d+)/) || href.match(/currentJobId=(\d+)/);
      if (match) ids.add(match[1]);
    }
    return ids;
  };
  const otherPostingIds = (node) => [...postingIds(node)].filter((id) => id !== currentId);
  // One stray reference — a "similar jobs" cross-link — is not a list. Several are.
  const holdsTheJobList = (node) => otherPostingIds(node).length > 1;
  const bigEnough = (node) =>
    node && longerText(node).length > 500 && !holdsTheJobList(node);

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
  tried.push(`other_postings_in_body:${otherPostingIds(document.body).length}`);
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
    if (!found) {
      // Last resort. On a search page the body contains the rail, so this pane is usable
      // only when it is not the whole layout. Saying "I could not read this" is correct;
      // sending the list would attribute one posting's text to another.
      found = { pane: document.body, matched: "fallback_body" };
      if (holdsTheJobList(document.body)) found.matched = "fallback_body_rejected_job_list";
    }

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
  // A rejected fallback contributes no text: an empty read reports itself honestly, while
  // the rail's text would be read as this posting's description.
  let text = reading.matched === "fallback_body_rejected_job_list"
    ? "" : (reading.bodyText || longerText(pane));
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
    diagnostics: { tried: [...tried, `matched:${matched}`,
                          `description_candidates:${descriptions.length}`,
                          `other_postings_in_pane:${otherPostingIds(pane).length}`],
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
    ? t("waiting") : t("reading");
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
    // The card the panel is currently showing, and the judgement shown with it. A decision
    // files this one, so it can never file a job — or a verdict — other than the one on
    // screen. The judgement travels because a reply months later has to be weighable
    // against the call that preceded it, and by then the directions and the ontology will
    // have moved; recomputing would answer a different question.
    lastReading = { job: body.job, page, body };
    lastPostingKey = key;
    lastPostingSnapshot = { postingId: page.postingId, bodySignature: page.bodySignature };
  } catch (error) {
    if (generation === readGeneration) {
      $("status").textContent = t("unreadableMessage");
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

// ---- filling one page, in a separate guarded window -------------------------------------
//
// A different mode from reading, wired separately on purpose. Nothing above can reach these
// functions: `readPosting` never calls them, and neither does `followUser`, so the automatic
// re-read that follows the user between postings cannot start a run.
//
// There is no `chrome.tabs`, no `chrome.scripting` and no URL anywhere in this section, and
// that is the design rather than an omission. The run does not happen in the user's tab, so
// what the panel can see of that tab is not evidence about anything — reporting it would only
// invite the bridge to trust it. The bridge resolves an opaque execution id against its own
// protected state and the execution authority supplies the target.
//
// The execution id is held here, in memory, for as long as the panel is open. It is
// deliberately not written to `chrome.storage.local`: a capability that outlives the window
// it was granted in is a capability nobody is watching. (The bridge token above is stored,
// which is existing browser-assist behaviour and is left as it is.)
const fillState = { executionId: "", busy: false };

function fillBusy(busy) {
  fillState.busy = busy;
  // The first of three layers against a double press. It is the one that talks to the user;
  // the bridge refuses a second execute, and the authority consumes the grant exactly once.
  $("fill-prepare").disabled = busy;
  $("fill-run").disabled = busy || !fillState.executionId;
}

async function fillPost(path, body) {
  const response = await fetch(`${state.endpoint}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Jobloom-Token": state.token },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `bridge_${response.status}`);
  return payload;
}

const fillCounts = (counts) => {
  const entries = Object.entries(counts || {});
  return entries.length ? entries.map(([name, n]) => `${name} ${n}`).join(", ") : t("fillNone");
};

function fillRow(list, label, value) {
  const term = document.createElement("dt");
  term.textContent = t(label);
  const detail = document.createElement("dd");
  // textContent, never innerHTML: every string here came back over the wire, and a summary
  // is not a place to start parsing markup.
  detail.textContent = value;
  list.append(term, detail);
}

function renderPrepared(prepared) {
  const list = $("fill-summary");
  list.textContent = "";
  fillRow(list, "fillRowApplication",
          `${prepared.application.application_id} · ${prepared.application.employer} · ${prepared.application.role}`);
  fillRow(list, "fillRowPage",
          `${t("fillPageIndex", { n: prepared.page.page_index + 1 })} · ` +
          t(prepared.page.final_page ? "fillFinalPage" : "fillNotFinalPage"));
  fillRow(list, "fillRowActions", String(prepared.actions.count));
  fillRow(list, "fillRowControls", fillCounts(prepared.actions.controls));
  fillRow(list, "fillRowOperations", fillCounts(prepared.actions.operations));
  fillRow(list, "fillRowSources", fillCounts(prepared.actions.sources));
  const risks = [...(prepared.risks.legal_items || []), ...(prepared.risks.restricted_requests || [])];
  fillRow(list, "fillRowRisks", risks.length ? risks.join(", ") : t("fillNone"));
  fillRow(list, "fillRowExpires", prepared.expires_at);
  fillRow(list, "fillRowBoundary", t("fillRowSubmitBoundary"));
  list.hidden = false;
}

function renderFinished(done) {
  const list = $("fill-summary");
  list.textContent = "";
  fillRow(list, "fillRowVerified", String(done.verified));
  fillRow(list, "fillRowPaused", String(done.paused));
  fillRow(list, "fillRowPending", String(done.pending));
  fillRow(list, "fillRowReasons", done.reasons.length ? done.reasons.join(", ") : t("fillNone"));
  fillRow(list, "fillRowConsumed", t(done.package_consumed ? "fillYes" : "fillNo"));
  fillRow(list, "fillRowBoundary", t("fillRowSubmitBoundary"));
  list.hidden = false;
}

$("fill-prepare").addEventListener("click", async () => {
  if (fillState.busy) return;
  const applicationId = $("fill-application").value.trim();
  if (!applicationId) {
    $("fill-status").textContent = t("fillNeedsApplication");
    return;
  }
  // A new preparation abandons any previous one rather than keeping two live: the run button
  // must never be ambiguous about which page it would run.
  fillState.executionId = "";
  fillBusy(true);
  $("fill-status").textContent = t("fillPreparing");
  try {
    const prepared = await fillPost("/fill/prepare", { application_id: applicationId });
    fillState.executionId = prepared.execution_id;
    renderPrepared(prepared);
    $("fill-status").textContent = t("fillPrepared");
  } catch (error) {
    $("fill-summary").hidden = true;
    $("fill-status").textContent = t("fillFailed", { code: error.message });
  } finally {
    fillBusy(false);
  }
});

$("fill-run").addEventListener("click", async () => {
  if (fillState.busy || !fillState.executionId) return;
  const executionId = fillState.executionId;
  // Spent the moment it is used. A second press has nothing to send, which is what makes the
  // panel's own de-duplication independent of how fast the button was pressed.
  fillState.executionId = "";
  fillBusy(true);
  $("fill-status").textContent = t("fillRunning");
  try {
    const done = await fillPost("/fill/execute", { execution_id: executionId });
    renderFinished(done);
    // Never "submitted". One page ran; the form was not sent, and nothing here observed
    // anything that could say otherwise.
    $("fill-status").textContent = t("fillDone");
  } catch (error) {
    $("fill-status").textContent = t("fillFailed", { code: error.message });
  } finally {
    fillBusy(false);
  }
});

loadSettings();
refreshAccess();
