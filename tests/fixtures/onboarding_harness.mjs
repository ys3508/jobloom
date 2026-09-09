// Runs the real onboarding page's script against a stub DOM and reports where it landed.
//
// The bug this exists for was a navigation bug: every endpoint answered correctly and the
// page still could not reach the carry, because the route to it was conditioned on something
// unrelated. No assertion over the source text could have caught that — the condition read
// perfectly sensibly — so this loads the shipped `<script>`, answers its requests, and
// reports the screen it actually chose and the buttons that screen offers.
//
// Responses are canned. Nothing here mocks the service's rules; what is under test is the
// page's own routing.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const pagePath = join(here, "..", "..", "skills", "jobloom", "assets", "onboarding.html");
const plan = JSON.parse(process.argv[2] || "{}");

const source = readFileSync(pagePath, "utf8");
const script = source.slice(source.indexOf("<script>") + "<script>".length,
                            source.lastIndexOf("</script>"));

const requests = [];

// Seeded by the caller to stand for a tab that has already loaded the page once, and read
// back afterwards to show what the page left behind for its own reload. `throws` is the
// browser that refuses site data, where the accessor itself raises.
const session = new Map(Object.entries(plan.session || {}));
const sessionStorage = {
  getItem(key) {
    if (plan.sessionThrows) throw new Error("site data blocked");
    return session.has(key) ? session.get(key) : null;
  },
  setItem(key, value) {
    if (plan.sessionThrows) throw new Error("site data blocked");
    session.set(key, String(value));
  },
};

class Node {
  constructor(tag) {
    this.tag = tag;
    this.className = "";
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this._text = "";
    this.value = "";
  }
  get textContent() { return this._text; }
  set textContent(value) { this._text = value; }
  addEventListener(kind, handler) { (this.listeners[kind] ||= []).push(handler); }
  setAttribute(key, value) { this.attributes[key] = value; }
  append(...kids) { this.children.push(...kids); }
  replaceChildren(...kids) { this.children = kids; }
  // What a person would see: this node's own text, then its children's, in order.
  get shownText() {
    return [this._text, ...this.children.map((kid) => kid.shownText)]
      .filter(Boolean).join(" | ");
  }
  buttons() {
    const mine = this.tag === "button" ? [this._text] : [];
    return mine.concat(...this.children.map((kid) => kid.buttons()));
  }
}

const byId = new Map();
const document = {
  createElement: (tag) => new Node(tag),
  getElementById: (id) => {
    if (!byId.has(id)) byId.set(id, new Node(id));
    return byId.get(id);
  },
  documentElement: { lang: "" },
};

async function fetchStub(path, options) {
  requests.push({ path, method: options?.method || "GET",
                  token: options?.headers?.["X-Jobloom-Token"] });
  const canned = plan.responses?.[path];
  if (canned === undefined) {
    return { ok: false, json: async () => ({ error: "no_canned_response", detail: path }) };
  }
  return { ok: true, json: async () => canned };
}

const context = vm.createContext({
  document,
  fetch: fetchStub,
  location: { search: plan.urlToken === null ? "" : `?token=${plan.urlToken || "harness"}`,
              pathname: "/" },
  sessionStorage,
  history: { replaceState() {} },
  navigator: { language: plan.language || "en" },
  URLSearchParams,
  JSON, Object, Array, Promise, Error, String, Number, Boolean, Math, console,
  setTimeout, clearTimeout, URL, Blob: class {},
});

vm.runInContext(script, context, { filename: "onboarding.html" });

// The boot is an async IIFE with awaits in it; let the microtask queue drain.
await new Promise((resolve) => setTimeout(resolve, 50));

const screen = document.getElementById("screen");
process.stdout.write(JSON.stringify({
  requests,
  token: vm.runInContext("TOKEN", context),
  session: Object.fromEntries(session),
  screen: vm.runInContext("app.screen", context),
  entry: vm.runInContext("app.entry", context),
  stranded: vm.runInContext("app.stranded.length", context),
  shown: screen.shownText,
  buttons: screen.buttons(),
}));
