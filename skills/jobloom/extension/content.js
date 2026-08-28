// Reads the posting the user already has on screen. Nothing here fetches, navigates, or
// clicks: every code path starts from an explicit message sent when the user asks for a
// reading, and ends by returning text that was already rendered for them.

const JOB_CONTAINERS = [
  // Unverified against the live sites. Kept as an ordered preference list with a plain
  // text fallback, so a redesign degrades the reading instead of breaking it.
  ".jobs-search__job-details--container",
  ".job-view-layout",
  "#jobsearch-ViewjobPaneWrapper",
  ".jobsearch-JobComponent",
  "main"
];

function jobPane() {
  for (const selector of JOB_CONTAINERS) {
    const node = document.querySelector(selector);
    if (node && (node.innerText || "").trim().length > 400) return node;
  }
  return document.body;
}

function readPosting() {
  const pane = jobPane();
  const text = (pane.innerText || "").replace(/\n{3,}/g, "\n\n").trim();
  return {
    url: location.href.split("?")[0],
    text: text.slice(0, 60000),
    title: (document.querySelector("h1")?.innerText || "").trim(),
    container: pane === document.body ? "fallback_body" : "job_pane"
  };
}

chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  // The only entry point. There is no observer, no interval, and no navigation hook, so
  // the script does nothing at all until the user asks.
  if (message?.type !== "jobloom:read-visible-posting") return false;
  try {
    respond({ ok: true, page: readPosting() });
  } catch (error) {
    respond({ ok: false, error: String(error).slice(0, 200) });
  }
  return true;
});
