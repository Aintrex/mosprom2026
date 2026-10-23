// Contract behavior in a minimal DOM, no browser/network/provider.
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("plugin/AIAssistant/web/app.js", "utf8");
class Element {
  constructor() { this.children = []; this.listeners = {}; this.textContent = ""; this.value = ""; }
  appendChild(el) { this.children.push(el); }
  removeChild(el) { this.children.splice(this.children.indexOf(el), 1); }
  get firstChild() { return this.children[0]; }
  set innerHTML(value) { throw Error("Untrusted HTML insertion"); }
  addEventListener(kind, fn) { this.listeners[kind] = fn; }
  emit(kind) { this.listeners[kind]({preventDefault() {}}); }
}
const idle = () => ({session_id: "session-test", messages: [], tool_events: [], agent_state: "idle", last_event_id: 0, slice: null});
const status = {llm: {configured: true, has_key: true}, agent_state: "idle", slicer: {machine_name: null, printing_mode: null}};
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
function harness(route, storage = new Map([["ste_ai_session_id", "session-test"]])) {
  const elements = {}, requests = [], timers = [];
  const context = {
    document: {getElementById(id) { return elements[id] || (elements[id] = new Element()); },
      createElement() { return new Element(); }, querySelectorAll() { return []; }},
    localStorage: {getItem: key => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    fetch: async (path, options) => {
      requests.push({path, options});
      const result = await route(path, options);
      return {ok: result.code < 400, status: result.code, json: async () => result.body};
    }, setInterval(fn) { timers.push(fn); }, console
  };
  vm.runInNewContext(source, context);
  return {elements, requests, storage, tick: () => timers[0]()};
}
async function main() {
  let snapshot = idle(), posts = 0, fail = true;
  const app = harness((path, options) => {
    if (path === "/api/status") return {code: 200, body: status};
    if (path.endsWith("/messages")) {
      posts++;
      const body = JSON.parse(options.body);
      if (fail) return {code: 500, body: {error: {message: "Acceptance unknown"}}};
      snapshot.messages = [{message_id: "accepted", client_message_id: body.client_message_id,
        role: "user", text: body.text, event_id: 1}];
      snapshot.last_event_id = 1;
      return {code: 202, body: {session_id: snapshot.session_id, message_id: "accepted", agent_state: "processing"}};
    }
    if (path.startsWith("/api/events")) return {code: 200, body: {events: [], next_after: snapshot.last_event_id, has_more: false}};
    return {code: 200, body: snapshot};
  });
  await settle();
  assert.equal(posts, 0, "Startup makes no agent request");
  assert.match(app.elements["status-badge"].textContent, /связь не проверена/);
  app.elements["chat-input"].value = "Измени слой";
  app.elements["chat-form"].emit("submit");
  await settle();
  const pending = JSON.parse(app.storage.get("ste_ai_pending"));
  assert(pending, "5xx retains the exact attempt");
  fail = false;
  app.elements["chat-form"].emit("submit");
  await settle();
  const bodies = app.requests.filter(r => r.path.endsWith("/messages")).map(r => r.options.body);
  assert.equal(bodies.length, 2);
  assert.equal(bodies[0], bodies[1], "Explicit retry uses the same complete body and ID");
  assert(!app.storage.has("ste_ai_pending"));
  assert.equal(app.elements.messages.children.length, 1, "Canonical accepted user has no optimistic duplicate");

  snapshot = idle();
  snapshot.messages = [{message_id: "user", role: "user", text: "<img src=x onerror=bad()>", event_id: 1},
    {message_id: "assistant", role: "assistant", text: "Текст ответа", event_id: 4}];
  snapshot.tool_events = [
    {id: 2, type: "tool_started", data: {turn_id: "a", tool_call_id: "c", name: "list_models", arguments: {}}},
    {id: 3, type: "tool_finished", data: {turn_id: "a", tool_call_id: "c", name: "list_models", result: {ok: true, data: []}}},
    {id: 5, type: "tool_finished", data: {turn_id: "b", tool_call_id: "c", name: "list_models", result: {ok: false, error: {code: "error"}}}}
  ];
  snapshot.last_event_id = 5;
  snapshot.slice = {slice_id: "slice-a", state: "completed", valid: false, progress: 1, error: null};
  let expired = true, eventCalls = 0, blockedResolve;
  const restored = harness(async path => {
    if (path === "/api/status") return {code: 200, body: status};
    if (path.startsWith("/api/events")) {
      eventCalls++;
      if (expired) { expired = false; return {code: 410, body: {error: {message: "expired"}}}; }
      await new Promise(resolve => { blockedResolve = resolve; });
      return {code: 200, body: {events: [], next_after: 5, has_more: false}};
    }
    return {code: 200, body: snapshot};
  });
  await settle();
  assert.equal(restored.elements.messages.children.length, 4, "Both participants and two turns' cards restored");
  assert.equal(restored.elements.messages.children[0].textContent, snapshot.messages[0].text);
  assert.match(restored.elements["slice-state"].textContent, /УСТАРЕЛ/);
  restored.tick();
  await settle();
  assert.equal(restored.elements.messages.children.length, 4, "410 snapshot resync has no duplicates");
  restored.tick(); restored.tick();
  await settle();
  assert.equal(eventCalls, 2, "Only one polling request is in flight");
  blockedResolve();
  await settle();
  assert(restored.requests.filter(r => r.path.startsWith("/api/events")).every(r => r.path.includes("after=5")));

  let pages = 0;
  const paginated = harness(path => {
    if (path === "/api/status") return {code: 200, body: status};
    if (!path.startsWith("/api/events")) return {code: 200, body: idle()};
    pages++;
    const id = pages === 1 ? 1 : 2;
    return {code: 200, body: {events: [{id, type: "assistant_message", data: {message_id: "a" + id, role: "assistant", text: "page"}}],
      next_after: id, has_more: pages === 1}};
  });
  await settle();
  paginated.tick(); await settle();
  assert.equal(pages, 2);
  assert.equal(paginated.elements.messages.children.length, 2);
  assert(paginated.requests.some(r => r.path.endsWith("after=1")));
  console.log("Agent web contract: 5 scenarios OK (offline DOM, no visual/native acceptance).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
