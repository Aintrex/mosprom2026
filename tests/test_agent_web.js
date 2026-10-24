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
    }, FormData: class { constructor() { this.parts = []; } append(key, value) { this.parts.push([key, value]); } },
    setInterval(fn) { timers.push(fn); }, console
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
  let uploadRelease, uploadCount = 0, fileSnapshot = idle();
  const withFiles = harness(async (path, options) => {
    if (path === "/api/status") return {code: 200, body: status};
    if (path === "/api/files") {
      uploadCount++;
      assert.equal(options.headers, undefined, "Browser sets multipart boundary");
      assert.equal(options.body.parts[0][0], "file");
      if (uploadCount === 1) await new Promise(resolve => { uploadRelease = resolve; });
      return {code: 201, body: {file_id: "file-" + uploadCount, name: options.body.parts[0][1].name,
        kind: uploadCount === 1 ? "image" : "model", size_bytes: 12, mime_type: "test/type"}};
    }
    if (path.endsWith("/messages")) return {code: 500, body: {error: {message: "Unknown acceptance"}}};
    return {code: 200, body: fileSnapshot};
  });
  await settle();
  withFiles.elements["chat-files"].files = [{name: "<photo>.png", size: 12}, {name: "part.stl", size: 12}];
  withFiles.elements["chat-files"].emit("change");
  await settle();
  assert(withFiles.elements["btn-send"].disabled, "No message while upload is unfinished");
  assert.equal(uploadCount, 1, "Uploads are sequential");
  withFiles.elements["chat-input"].value = "Объясни фото";
  withFiles.elements["chat-form"].emit("submit");
  assert(!withFiles.requests.some(r => r.path.endsWith("/messages")));
  uploadRelease(); await settle();
  assert.equal(uploadCount, 2);
  assert.equal(withFiles.elements["attachment-list"].children[0].textContent, "<photo>.png — загружен");
  withFiles.elements["attachment-list"].children[1].children[0].emit("click");
  withFiles.elements["chat-form"].emit("submit"); await settle();
  const fileBody = withFiles.requests.find(r => r.path.endsWith("/messages")).options.body;
  assert.deepEqual(JSON.parse(fileBody).attachment_ids, ["file-1"], "Removed file is not sent");
  assert.deepEqual(JSON.parse(withFiles.storage.get("ste_ai_pending")).attachment_ids, ["file-1"]);

  let retryUploads = 0;
  const fileRetry = harness((path, options) => {
    if (path === "/api/status") return {code: 200, body: status};
    if (path === "/api/files") { retryUploads++; throw Error("Must reuse uploaded ID"); }
    if (path.endsWith("/messages")) {
      assert.equal(options.body, fileBody, "Refresh preserves exact body, file IDs and client_message_id");
      const body = JSON.parse(options.body);
      fileSnapshot.messages = [{message_id: "file-user", client_message_id: body.client_message_id,
        role: "user", text: body.text, attachment_ids: body.attachment_ids,
        attachments: [{file_id: "file-1", name: "<photo>.png"}]}];
      return {code: 202, body: {}};
    }
    return {code: 200, body: fileSnapshot};
  }, withFiles.storage);
  await settle();
  assert(fileRetry.elements["chat-files"].disabled, "Pending file IDs are frozen");
  assert.equal(fileRetry.elements["attachment-list"].children[0].textContent, "<photo>.png — загружен");
  fileRetry.elements["chat-form"].emit("submit"); await settle();
  assert.equal(retryUploads, 0);
  assert.match(fileRetry.elements.messages.children[0].textContent, /Вложение: <photo>.png/);
  assert(!fileRetry.storage.has("ste_ai_pending"));

  const badFile = harness(path => {
    if (path === "/api/status") return {code: 200, body: status};
    if (path === "/api/files") return {code: 415, body: {error: {message: "Неверный формат"}}};
    return {code: 200, body: idle()};
  });
  await settle();
  badFile.elements["chat-files"].files = [{name: "bad.stl", size: 1}];
  badFile.elements["chat-files"].emit("change"); await settle();
  assert.match(badFile.elements["attachment-list"].children[0].textContent, /Неверный формат/);
  assert(badFile.elements["btn-send"].disabled);
  badFile.elements["attachment-list"].children[0].children[0].emit("click");
  assert(!badFile.elements["btn-send"].disabled);
  let artifactPage = 0;
  const downloadable = {artifact_id: "artifact-1", file_name: "<result>.gcode", kind: "gcode", size_bytes: 123,
    slice_id: "slice-1", valid: true, file_id: "download-1", download_url: "/api/files/download-1"};
  const localOnly = {artifact_id: "artifact-old", file_name: "old.3mf", kind: "project", size_bytes: 50, slice_id: "old", valid: true};
  const unsafe = Object.assign({}, downloadable, {artifact_id: "artifact-unsafe", download_url: "javascript:alert(1)"});
  const artifactSnapshot = Object.assign(idle(), {artifacts: [downloadable, localOnly, unsafe]});
  const artifactApp = harness(path => {
    if (path === "/api/status") return {code: 200, body: status};
    if (path.startsWith("/api/events")) {
      artifactPage++;
      if (artifactPage === 3) return {code: 410, body: {error: {message: "expired"}}};
      const artifact = artifactPage === 1 ? downloadable : Object.assign({}, downloadable, {valid: false});
      if (artifactPage === 2) artifactSnapshot.artifacts[0] = artifact;
      return {code: 200, body: {events: [{id: artifactPage, type: "artifact_ready", data: {artifact}}], next_after: artifactPage, has_more: false}};
    }
    return {code: 200, body: artifactSnapshot};
  });
  await settle();
  const rows = artifactApp.elements.artifacts.children;
  assert.equal(rows.length, 3);
  assert.match(rows[0].textContent, /<result>.gcode/);
  assert.equal(rows[0].children[0].href, downloadable.download_url);
  assert.equal(rows[0].children[0].rel, "noopener");
  assert.equal(rows[1].children.length, 0, "Historical local artifact is not a fake download");
  assert.equal(rows[2].children.length, 0, "Unsafe href is never assigned");
  artifactApp.tick(); await settle();
  assert.equal(rows.length, 3, "Repeated artifact ID updates the same card");
  artifactApp.tick(); await settle();
  assert.equal(rows[0].children.length, 0, "Known stale event removes the link");
  assert.match(rows[0].textContent, /устарел/);
  artifactApp.tick(); await settle();
  assert.equal(artifactApp.elements.artifacts.children.length, 3, "410 snapshot restores artifacts without duplicates");
  assert.equal(artifactApp.elements.artifacts.children[0].children.length, 0);
  const watching = {state: "watching", error: null}, finished = {state: "finished", error: null};
  const operationSnapshot = Object.assign(idle(), {
    slice: {slice_id: "owned-slice", state: "running", progress: 0.25, valid: false, result: null, observation: watching},
    export: {export_id: "owned-export", slice_id: "owned-slice", kind: "gcode", state: "queued", progress: null, valid: false, observation: watching}
  });
  const finalSlice = Object.assign({}, operationSnapshot.slice, {state: "completed", progress: 1, valid: true, observation: finished,
    result: {source: "ste_slicer", print_time_seconds: 3723, material: {length_mm: 1270, weight_g: 3.6}}});
  const finalExport = Object.assign({}, operationSnapshot.export, {state: "completed", valid: true, observation: finished});
  let operationPage = 0;
  const operations = harness(path => {
    if (path === "/api/status") return {code: 200, body: Object.assign({}, status, {capabilities: {background_slice_events: true}})};
    if (path.startsWith("/api/events")) {
      operationPage++;
      return {code: 200, body: {events: operationPage === 1 ? [
        {id: 1, type: "agent_state", data: {state: "idle"}},
        {id: 2, type: "slice_finished", data: finalSlice},
        {id: 3, type: "export_progress", data: finalExport},
        {id: 4, type: "artifact_ready", data: {artifact: downloadable}}
      ] : [], next_after: 4, has_more: false}};
    }
    return {code: 200, body: operationSnapshot};
  });
  await settle();
  assert.match(operations.elements["slice-state"].textContent, /обновляется автоматически без запросов LLM/);
  assert.match(operations.elements["export-state"].textContent, /queued/);
  operations.tick(); await settle();
  assert.match(operations.elements["slice-state"].textContent, /completed/);
  assert.match(operations.elements["slice-state"].textContent, /3723/);
  assert.match(operations.elements["export-state"].textContent, /completed/);
  assert.equal(operations.elements.artifacts.children[0].children[0].href, downloadable.download_url);
  assert(!operations.requests.some(r => r.options && r.options.method === "POST"), "Background UI does not initiate LLM/tools/mutations");

  const stopped = {state: "stopped", error: {code: "observation_interrupted", message: "<read failed>"}};
  operationSnapshot.slice = Object.assign({}, operationSnapshot.slice, {observation: stopped});
  operationSnapshot.export = Object.assign({}, operationSnapshot.export, {observation: stopped});
  let observationExpired = true;
  const stoppedApp = harness(path => {
    if (path === "/api/status") return {code: 200, body: status};
    if (path.startsWith("/api/events") && observationExpired) {
      observationExpired = false;
      return {code: 410, body: {error: {message: "expired"}}};
    }
    return {code: 200, body: operationSnapshot};
  });
  await settle();
  assert.match(stoppedApp.elements["slice-state"].textContent, /Наблюдение прервано: <read failed>/);
  assert.match(stoppedApp.elements["slice-state"].textContent, /engine мог продолжиться/);
  stoppedApp.tick(); await settle();
  assert.match(stoppedApp.elements["export-state"].textContent, /queued/);
  assert.match(stoppedApp.elements["export-state"].textContent, /Наблюдение прервано/);
  assert.equal(stoppedApp.elements.messages.children.length, 0, "Observer restoration invents no LLM tool cards");
  const argumentFailure = {ok: false, error: {code: "invalid_tool_arguments", stage: "validation", executed: false,
    message: "Значение не соответствует типу float", arguments_summary: {format: "json_object", field_types: {value: "string"}},
    expected: {required: ["key", "scope", "value"]}}};
  const rejectedSnapshot = Object.assign(idle(), {last_event_id: 2, tool_events: [
    {id: 1, type: "tool_finished", data: {turn_id: "rejected", tool_call_id: "bad", name: "set_setting", result: argumentFailure}},
    {id: 2, type: "tool_finished", data: {turn_id: "historical", tool_call_id: "old", name: "set_setting",
      result: {ok: false, error: {code: "invalid_tool_arguments", message: "Old result"}}}}
  ]});
  let rejectionExpired = true;
  const rejectedApp = harness(path => {
    if (path === "/api/status") return {code: 200, body: status};
    if (path.startsWith("/api/events") && rejectionExpired) {
      rejectionExpired = false;
      return {code: 410, body: {error: {message: "expired"}}};
    }
    return {code: 200, body: rejectedSnapshot};
  });
  await settle();
  let rejectedRows = rejectedApp.elements.messages.children;
  assert.match(rejectedRows[0].children[0].textContent, /Отклонено до запуска/);
  assert.match(rejectedRows[0].children[1].textContent, /отказ проверки до запуска/);
  assert.match(rejectedRows[0].children[1].textContent, /float/);
  assert(!rejectedRows[0].children[1].textContent.includes("Аргументы: null"));
  assert.match(rejectedRows[1].children[1].textContent, /не опубликованы/);
  assert(!rejectedRows[1].children[0].textContent.includes("до запуска"), "Historical error has no execution proof");
  rejectedApp.tick(); await settle();
  rejectedRows = rejectedApp.elements.messages.children;
  assert.equal(rejectedRows.length, 2, "410 preserves validation diagnostics without inventing started");

  let correctionPage = 0;
  const correctedArgs = {key: "layer_height", scope: "global", value: 0.12};
  const correctionApp = harness(path => {
    if (path === "/api/status") return {code: 200, body: status};
    if (path.startsWith("/api/events")) {
      correctionPage++;
      return {code: 200, body: {events: correctionPage === 1 ? [
        {id: 1, type: "tool_finished", data: {turn_id: "x", tool_call_id: "bad", name: "set_setting", result: argumentFailure}},
        {id: 2, type: "tool_started", data: {turn_id: "x", tool_call_id: "corrected", name: "set_setting", arguments: correctedArgs}},
        {id: 3, type: "tool_finished", data: {turn_id: "x", tool_call_id: "corrected", name: "set_setting", result: {ok: true, data: {after: 0.12}}}}
      ] : [], next_after: 3, has_more: false}};
    }
    return {code: 200, body: idle()};
  });
  await settle(); correctionApp.tick(); await settle();
  const correctedRows = correctionApp.elements.messages.children;
  assert.equal(correctedRows.length, 2);
  assert.match(correctedRows[0].children[0].textContent, /Отклонено до запуска/);
  assert.match(correctedRows[1].children[1].textContent, /Аргументы:.*"value":0.12/);
  assert.equal(correctedRows[1].className, "tool-card ok");
  assert(!correctionApp.requests.some(r => r.options && r.options.method === "POST"), "UI never retries the rejected mutation");
  console.log("Agent web contract: 14 scenarios OK (offline DOM, no visual/native acceptance).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
