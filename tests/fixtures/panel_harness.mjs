// Runs the real `panel.js` against stubbed browser APIs and reports what it actually did.
//
// A string search over the source can say the word `chrome.tabs` is absent from a section;
// it cannot say that pressing the fill button does not reach it, because the call could come
// from anywhere the handler leads. This loads the shipped file, presses the buttons, and
// records every API call and request in order, so the assertions are about behaviour.
//
// Nothing here is a mock of the bridge's rules. Responses are canned so the panel has
// something to render; what is being tested is the panel's outbound behaviour.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const panelPath = join(here, "..", "..", "skills", "jobloom", "extension", "panel.js");
const plan = JSON.parse(process.argv[2] || "{}");

const trace = { calls: [], requests: [], storage: {}, status: {}, summary: [] };
const note = (name, detail) => trace.calls.push({ name, detail });

class Node {
  constructor(id) {
    this.id = id;
    this.listeners = {};
    this.dataset = {};
    this.children = [];
    this._text = "";
    this.value = plan.values?.[id] ?? "";
    this.hidden = false;
    this.disabled = false;
    this.open = false;
    this.classList = { toggle() {}, add() {}, remove() {} };
  }
  get textContent() { return this._text; }
  set textContent(value) {
    this._text = value;
    if (value === "") this.children = [];
    if (this.id) trace.status[this.id] = value;
  }
  addEventListener(kind, handler) { (this.listeners[kind] ||= []).push(handler); }
  setAttribute() {}
  append(...nodes) {
    this.children.push(...nodes);
    if (this.id === "fill-summary") {
      trace.summary = this.children.map((child) => child.textContent);
    }
  }
  querySelectorAll() { return []; }
  async click() {
    for (const handler of this.listeners.click || []) await handler({});
  }
}

const nodes = new Map();
const byId = (id) => {
  if (!nodes.has(id)) nodes.set(id, new Node(id));
  return nodes.get(id);
};

const document = {
  documentElement: { lang: "" },
  getElementById: byId,
  createElement: () => new Node(""),
  querySelectorAll: (selector) => (plan.query?.[selector] || []).map(byId),
};

const navigationListeners = [];
const chrome = {
  storage: {
    local: {
      async get(keys) {
        note("storage.get", keys);
        const out = {};
        for (const key of keys) if (key in trace.storage) out[key] = trace.storage[key];
        return out;
      },
      async set(values) {
        note("storage.set", values);
        Object.assign(trace.storage, values);
      },
    },
  },
  permissions: {
    async contains() { note("permissions.contains"); return plan.pageAccess === true; },
    async request() { note("permissions.request"); return true; },
  },
  tabs: {
    async query(q) { note("tabs.query", q); return [{ id: 7 }]; },
  },
  scripting: {
    async executeScript(options) {
      note("scripting.executeScript", { tabId: options?.target?.tabId });
      return [{ result: { url: "https://www.linkedin.com/jobs/view/1", text: "a posting",
                          postingId: "1", bodySignature: "sig" } }];
    },
  },
  webNavigation: {
    onHistoryStateUpdated: { addListener: (fn) => navigationListeners.push(fn) },
    onCompleted: { addListener: (fn) => navigationListeners.push(fn) },
  },
};

async function fetchStub(url, options = {}) {
  const path = String(url).replace(plan.endpoint || "http://127.0.0.1:8787", "");
  trace.requests.push({
    path,
    method: options.method || "GET",
    body: options.body ? JSON.parse(options.body) : null,
    headers: Object.keys(options.headers || {}).sort(),
  });
  const canned = (plan.responses || {})[path];
  if (canned === undefined) return { ok: false, status: 404, json: async () => ({ error: "no_stub" }) };
  return { ok: canned.ok !== false, status: canned.status || 200, json: async () => canned.body };
}

trace.storage = { ...(plan.storage || {}) };

const context = vm.createContext({
  document, chrome, fetch: fetchStub, console,
  setTimeout, clearTimeout, URL, JSON, Object, Array, String, Number, Boolean, Math, Error,
});
vm.runInContext(readFileSync(panelPath, "utf8"), context, { filename: "panel.js" });

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
await settle();
await settle();

for (const step of plan.steps || []) {
  if (step.click) {
    if (step.values) for (const [id, value] of Object.entries(step.values)) byId(id).value = value;
    if (step.twice) {
      // Both presses issued before either settles: this is the double-click, not two clicks.
      await Promise.all([byId(step.click).click(), byId(step.click).click()]);
    } else {
      await byId(step.click).click();
    }
  }
  if (step.navigate) {
    for (const listener of navigationListeners) await listener({ frameId: 0, tabId: 7 });
  }
  await settle();
  await settle();
}
await settle();

trace.navigationListeners = navigationListeners.length;
process.stdout.write(JSON.stringify(trace));
