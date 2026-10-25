// Contract behavior in a minimal DOM, no browser/network/provider.
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("plugin/AIAssistant/web/app.js", "utf8");
class Element {
  constructor(tag = "div") { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this._text = ""; this.value = ""; this.open = false; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  appendChild(el) { if (el.parentNode) el.parentNode.removeChild(el); this.children.push(el); el.parentNode = this; return el; }
  removeChild(el) { assert(this.children.includes(el)); this.children.splice(this.children.indexOf(el), 1); el.parentNode = null; }
  get firstChild() { return this.children[0]; }
  get firstElementChild() { return this.children[0]; }
  setAttribute(key, value) { (this.attrs || (this.attrs = {}))[key] = String(value); }
  getAttribute(key) { return this.attrs && this.attrs[key] || null; }
  querySelector(tag) { return nodes(this, tag)[0]; }
  querySelectorAll(tag) { return nodes(this, tag); }
  set innerHTML(value) { throw Error("Untrusted HTML insertion"); }
  addEventListener(kind, fn) { this.listeners[kind] = fn; }
  emit(kind, event = {}) { this.listeners[kind]({preventDefault() {}, ...event}); }
  focus() { this.focused = true; }
}
// Scene panel may read list_models/get_model_state via /mcp; nothing else is sent unprompted.
function readOnlyRpc(r) { return r.path === "/mcp" && ["list_models", "get_model_state"].includes(JSON.parse(r.options.body).params.name); }
function field(app, id) { return app.elements[id] || (app.elements[id] = new Element("input")); }
function lastChild(node) { return node.children[node.children.length - 1]; }
function nodes(root, tag) { return (root.tagName === tag.toUpperCase() ? [root] : []).concat(root.children.flatMap(child => nodes(child, tag))); }
function actionText(root) { return root.tagName === "DETAILS" && !(root.className || "").includes("action-group") ? "" : root._text + root.children.map(actionText).join(" "); }
function visibleText(root) { return root.tagName === "DETAILS" && !root.open ? (root.children[0] ? visibleText(root.children[0]) : "") : root._text + root.children.filter(child => !child.hidden).map(visibleText).join(" "); }
function toolRows(app) { return nodes(app.elements.messages, "div").filter(node => /^tool-card /.test(node.className || "")); }
function actionGroups(app) { return nodes(app.elements.messages, "details").filter(node => /action-group/.test(node.className || "")); }
function technicalNodes(root) { return nodes(root, "details").filter(node => node.className === "technical-details"); }
function chatOpen(app, index) { return app.elements["chat-list"].children[index].children[0]; }
function chatDelete(app, index) { return app.elements["chat-list"].children[index].children[1]; }
const idle = () => ({session_id: "session-test", messages: [], tool_events: [], agent_state: "idle", last_event_id: 0, slice: null});
const status = {llm: {configured: true, has_key: true}, agent_state: "idle", slicer: {machine_name: null, printing_mode: null}};
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
function harness(route, storage = new Map([["ste_ai_session_id", "session-test"]])) {
  const elements = {}, requests = [], timers = [], confirmations = [], confirmation = {allow: true};
  const context = {
    document: {getElementById(id) { assert(!["btn-open-session", "btn-delete-session"].includes(id), "Removed DOM IDs must not be accessed"); return elements[id] || (elements[id] = new Element()); },
      createElementNS(ns, tag) { return new Element(tag); },
      createElement(tag) { return new Element(tag); }, createTextNode(text) { const node = new Element("#text"); node.textContent = text; return node; }, querySelectorAll() { return []; }},
    localStorage: {getItem: key => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    fetch: async (path, options) => {
      requests.push({path, options});
      const result = await route(path, options);
      return {ok: result.code < 400, status: result.code, json: async () => result.body};
    }, FormData: class { constructor() { this.parts = []; } append(key, value) { this.parts.push([key, value]); } },
    setInterval(fn) { timers.push(fn); }, confirm(text) { confirmations.push(text); return confirmation.allow; }, console, URL
  };
  for (const id of ["state-card", "scene-card", "connection-card"]) context.document.getElementById(id).appendChild(new Element("summary"));
  context.document.getElementById("file-message").appendChild(context.document.getElementById("artifacts"));
  vm.runInNewContext(source, context);
  return {elements, requests, storage, confirmation, confirmations, tick: () => timers[0]()};
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
  assert.equal(toolRows(restored).length, 2, "Both turns restored");
  assert.equal(actionGroups(restored).length, 2);
  assert.equal(restored.elements.messages.children[0].textContent, snapshot.messages[0].text);
  assert.match(restored.elements["slice-state"].textContent, /УСТАРЕЛ/);
  restored.tick();
  await settle();
  assert.equal(toolRows(restored).length, 2, "410 snapshot resync has no duplicates");
  assert.equal(actionGroups(restored).length, 2);
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
  assert.match(withFiles.elements["attachment-list"].children[0].textContent, /<photo>\.pngФото PNG · 12 Б.*Готово: прикреплено/);
  assert.match(withFiles.elements["attachment-list"].children[1].textContent, /3D-модель STL.*В сцену ещё не импортировано/);
  lastChild(withFiles.elements["attachment-list"].children[1]).emit("click");
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
  assert.match(fileRetry.elements["attachment-list"].children[0].textContent, /<photo>\.png.*Отправляется вместе с сообщением/);
  fileRetry.elements["chat-form"].emit("submit"); await settle();
  assert.equal(retryUploads, 0);
  assert.match(fileRetry.elements.messages.children[0].textContent, /<photo>.png/);
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
  assert.match(badFile.elements["attachment-status"].textContent, /Файлов с ошибкой: 1/);
  assert(badFile.elements["btn-send"].disabled);
  lastChild(badFile.elements["attachment-list"].children[0]).emit("click");
  assert(!badFile.elements["btn-send"].disabled);
  assert(badFile.elements["attachment-status"].hidden);
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
  assert.equal(nodes(rows[0], "a")[0].href, downloadable.download_url);
  assert.equal(nodes(rows[0], "a")[0].rel, "noopener");
  assert.equal(nodes(rows[1], "a").length, 0, "Historical local artifact is not a fake download");
  assert.equal(nodes(rows[2], "a").length, 0, "Unsafe href is never assigned");
  artifactApp.tick(); await settle();
  assert.equal(rows.length, 3, "Repeated artifact ID updates the same card");
  artifactApp.tick(); await settle();
  assert.equal(nodes(rows[0], "a").length, 0, "Known stale event removes the link");
  assert.match(rows[0].textContent, /Устарел/);
  assert.match(artifactApp.elements["file-message-title"].textContent, /доступность не подтверждена/);
  artifactApp.tick(); await settle();
  assert.equal(artifactApp.elements.artifacts.children.length, 3, "410 snapshot restores artifacts without duplicates");
  assert.equal(nodes(artifactApp.elements.artifacts.children[0], "a").length, 0);
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
  assert.match(operations.elements["export-state"].textContent, /ожидает запуска/);
  operations.tick(); await settle();
  assert.match(operations.elements["slice-state"].textContent, /Завершено/);
  assert.match(operations.elements["slice-state"].textContent, /1 ч 2 мин 3 с/);
  assert.match(operations.elements["export-state"].textContent, /Завершено/);
  assert.equal(nodes(operations.elements.artifacts.children[0], "a")[0].href, downloadable.download_url);
  assert(!operations.requests.some(r => r.options && r.options.method === "POST" && !readOnlyRpc(r)), "Background UI does not initiate LLM/tools/mutations");

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
  assert.match(stoppedApp.elements["slice-state"].textContent, /слайсер мог продолжить/);
  stoppedApp.tick(); await settle();
  assert.match(stoppedApp.elements["export-state"].textContent, /ожидает запуска/);
  assert.match(stoppedApp.elements["export-state"].textContent, /Наблюдение прервано/);
  assert.equal(toolRows(stoppedApp).length, 0, "Observer restoration invents no LLM tool cards");
  assert.match(visibleText(stoppedApp.elements.messages), /операция могла продолжиться/);
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
  let rejectedRows = toolRows(rejectedApp);
  assert.match(rejectedRows[0].children[0].textContent, /Отклонено до запуска/);
  assert.match(actionText(rejectedRows[0].children[1]), /API слайсера не вызывался/);
  assert.match(actionText(rejectedRows[0].children[1]), /числовому типу/);
  assert.match(nodes(rejectedRows[0], "details")[0].textContent, /float/);
  assert.equal(nodes(rejectedRows[0], "details")[0].open, false);
  assert(!rejectedRows[0].children[1].textContent.includes("Аргументы: null"));
  assert.match(rejectedRows[1].children[1].textContent, /не опубликованы/);
  assert(!rejectedRows[1].children[0].textContent.includes("до запуска"), "Historical error has no execution proof");
  rejectedApp.tick(); await settle();
  rejectedRows = toolRows(rejectedApp);
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
  const correctedRows = toolRows(correctionApp);
  assert.equal(correctedRows.length, 2);
  assert.match(correctedRows[0].children[0].textContent, /Отклонено до запуска/);
  assert.match(actionText(correctedRows[1]), /подтверждённое значение 0,12/);
  assert.match(nodes(correctedRows[1], "details")[0].textContent, /"value": 0.12/);
  assert.equal(correctedRows[1].className, "tool-card ok");
  assert(!correctionApp.requests.some(r => r.options && r.options.method === "POST" && !readOnlyRpc(r)), "UI never retries the rejected mutation");
  const mdText = "**Готово** и *важно*, `layer_height`.\nВторая строка.\n\n- первый\n- второй\n\n1. шаг\n2. шаг\n\n```json\n{\"text\":\"**буквально** <img onerror=x>\"}\n```\n[Справка](https://example.invalid/guide)";
  const mdSnapshot = Object.assign(idle(), {last_event_id: 1, messages: [{message_id: "md", role: "assistant", text: mdText, event_id: 1}]});
  let mdPage = 0;
  const mdApp = harness(path => {
    if (path === "/api/status") return {code: 200, body: status};
    if (path.startsWith("/api/events")) {
      if (++mdPage === 2) return {code: 410, body: {error: {message: "expired"}}};
      return {code: 200, body: {events: [{id: 2, type: "assistant_message", data: mdSnapshot.messages[0]}], next_after: 2, has_more: false}};
    }
    return {code: 200, body: mdSnapshot};
  });
  await settle();
  assert.equal(nodes(mdApp.elements.messages, "strong")[0].textContent, "Готово");
  assert.equal(nodes(mdApp.elements.messages, "em")[0].textContent, "важно");
  assert.equal(nodes(mdApp.elements.messages, "ul")[0].children.length, 2);
  assert.equal(nodes(mdApp.elements.messages, "ol")[0].children.length, 2);
  assert.equal(nodes(mdApp.elements.messages, "br").length, 1);
  assert.equal(nodes(mdApp.elements.messages, "pre")[0].textContent, '{"text":"**буквально** <img onerror=x>"}');
  assert.equal(nodes(mdApp.elements.messages, "strong").length, 1, "Code contents stay literal");
  assert.equal(nodes(mdApp.elements.messages, "a")[0].href, "https://example.invalid/guide");
  assert.equal(nodes(mdApp.elements.messages, "a")[0].rel, "noopener noreferrer");
  mdApp.tick(); await settle(); mdApp.tick(); await settle();
  assert.equal(mdApp.elements.messages.children.length, 1, "Repeat event/410 retains one formatted message");
  assert.equal(nodes(mdApp.elements.messages, "strong")[0].textContent, "Готово");

  const injection = '<script>alert(1)</script><img src=x onerror=alert(1)><svg onload=alert(1)>\n' +
    '[опасно](javascript:alert(1)) [data](data:text/html,x) [файл](file:///private.txt) [внешне](//evil.invalid) ' +
    '[ключ](https://user:password@example.invalid) ![фото](https://evil.invalid/image.png)';
  const hostileSnapshot = Object.assign(idle(), {messages: [
    {message_id: "hostile", role: "assistant", text: injection},
    {message_id: "literal-user", role: "user", text: "**не форматировать** <img src=x>"}
  ]});
  const hostileApp = harness(path => ({code: 200, body: path === "/api/status" ? status : hostileSnapshot}));
  await settle();
  for (const tag of ["script", "img", "svg", "iframe", "a"]) assert.equal(nodes(hostileApp.elements.messages, tag).length, 0, tag + " must stay inert");
  assert.match(hostileApp.elements.messages.textContent, /<script>alert\(1\)<\/script>/);
  assert.match(hostileApp.elements.messages.textContent, /!\[фото\]/);
  assert.equal(nodes(hostileApp.elements.messages.children[1], "strong").length, 0, "User text is not parsed as Markdown");
  assert(hostileApp.requests.every(r => r.path.startsWith("/api/") || readOnlyRpc(r)), "Markdown images trigger no external fetches");

  function toolSnapshot(fixtures) {
    const result = idle(); let id = 0;
    result.tool_events = fixtures.flatMap((fixture, index) => [
      {id: ++id, type: "tool_started", data: {turn_id: "readable", tool_call_id: "tool-" + index, name: fixture.name, arguments: fixture.args || {}}},
      {id: ++id, type: "tool_finished", data: {turn_id: "readable", tool_call_id: "tool-" + index, name: fixture.name,
        result: fixture.result || {ok: true, data: fixture.data}}}
    ]); result.last_event_id = id; return result;
  }
  const readableSnapshot = toolSnapshot([
    {name: "get_setting", data: {key: "layer_height", value: 0.1, unit: "mm"}},
    {name: "set_setting", data: {key: "layer_height", before: 0.1, after: 0.12, unit: "mm"}},
    {name: "load_model_file", data: {file_name: "**literal** <img>.stl", objects: [{object_id: "model-9", bounds: {size: {x: 10, y: 20, z: 30}, unit: "mm"}}]}},
    {name: "get_model_bounds", data: {object_id: "model-9", bounds: {size: {x: 10, y: 20, z: 30}, unit: "mm"}}},
    {name: "get_slicer_state", data: {connected: true, machine: {name: "Hybrid 530"}, printing_mode: "cylindrical", backend: {id: "CLIParserBackend"}}},
    {name: "get_setting", data: {key: "material_print_temperature", value: 210, unit: "°C"}},
    {name: "get_setting", data: {key: "speed_print", value: 50, unit: "mm/s"}},
    {name: "get_setting", data: {key: "line_width", value: 0.123456, unit: "unknown-unit"}},
    {name: "set_setting", data: {key: "support_enable", before: false, after: false, unit: ""}}
  ]);
  const readableApp = harness(path => ({code: 200, body: path === "/api/status" ? status : readableSnapshot}));
  await settle();
  const readable = actionText(readableApp.elements.messages);
  for (const phrase of ["Высота слоя: 0,1 мм", "0,1 → 0,12 мм", "Модель загружена: **literal** <img>.stl", "Объектов: 1", "X / Y / Z: 10 / 20 / 30 мм", "Принтер: Hybrid 530", "5D Spiral", "210 °С", "50 мм/с", "0,123456 unknown-unit", "значение не изменилось"]) assert(readable.includes(phrase), phrase);
  assert(!readable.includes('"before"') && !readable.includes('"objects"'), "No JSON in primary tool summaries");
  assert.equal(nodes(readableApp.elements.messages, "strong").length, 0, "Filename emphasis stays literal");
  assert.equal(nodes(readableApp.elements.messages, "img").length, 0);
  assert.equal(technicalNodes(readableApp.elements.messages).length, 9);
  assert(technicalNodes(readableApp.elements.messages).every(d => d.open === false));
  assert.match(technicalNodes(readableApp.elements.messages)[1].textContent, /"after": 0.12/);

  const unknownSnapshot = toolSnapshot([
    {name: "unknown_future_tool", data: {setting: "not verified", html: "<img onerror=x>"}},
    {name: "set_setting", data: {unexpected: true}},
    {name: "set_setting", data: {key: "layer_height", after: {bad: "object"}}},
    {name: "constructor", data: {future: true}},
    {name: "get_slicer_state", data: {connected: true, machine: {name: "Hybrid 530"}, printing_mode: "classic", backend: {id: "CLIParserBackend", matches_mode: false}}}
  ]);
  const unknownApp = harness(path => ({code: 200, body: path === "/api/status" ? status : unknownSnapshot}));
  await settle();
  assert(toolRows(unknownApp).every(row => row.className === "tool-card neutral"));
  assert.match(actionText(unknownApp.elements.messages), /формат результата не распознан/);
  assert(!actionText(unknownApp.elements.messages).includes("изменена"));
  assert.equal(nodes(unknownApp.elements.messages, "img").length, 0);
  assert.equal(toolRows(unknownApp)[3].children[0].textContent, "Инструмент", "Unknown names do not inherit dictionary labels");
  assert.match(actionText(toolRows(unknownApp)[4]), /Движок не соответствует выбранному режиму/);

  const errorsSnapshot = toolSnapshot([
    {name: "set_setting", result: {ok: false, error: {code: "qt_timeout", message: "Qt начал операцию, результат неизвестен"}}},
    {name: "get_slicing_result", result: {ok: false, error: {code: "slice_result_stale", message: "Проект изменён"}}},
    {name: "get_slicing_status", data: {slice_id: "failed", state: "failed", progress: null, valid: false, error: {code: "slicing_failed", message: "Ошибка движка"}}},
    {name: "get_slicing_status", data: {slice_id: "stale", state: "completed", progress: 1, valid: false}}
  ]);
  errorsSnapshot.messages = [{message_id: "timeout-msg", role: "assistant", is_error: true, event_id: 99,
    text: "Сетевой timeout в LLM-обращении №2: ожидание complete 15.125 с; выделенный бюджет 15.000 с. Подтверждённых изменяющих вызовов до отказа: 1"}];
  const errorsApp = harness(path => ({code: 200, body: path === "/api/status" ? status : errorsSnapshot}));
  await settle();
  const errorRows = toolRows(errorsApp).concat(errorsApp.elements.messages.children.filter(row => row.className === "msg error"));
  assert.match(actionText(errorRows[0]), /Исход операции требует проверки/);
  assert.match(actionText(errorRows[0]), /неизвестен/);
  assert(!actionText(errorRows[0]).includes("API слайсера не вызывался"));
  assert.match(actionText(errorRows[1]), /Результат неактуален/);
  assert.equal(errorRows[2].className, "tool-card fail");
  assert.match(actionText(errorRows[2]), /Ошибка движка/);
  assert.equal(errorRows[3].className, "tool-card neutral");
  assert.match(actionText(errorRows[3]), /УСТАРЕЛ/);
  assert.match(actionText(errorRows[4]), /Ответ языковой модели не получен вовремя/);
  assert(!actionText(errorRows[4]).includes("15.125"), "Raw timing stays in closed details");
  assert.match(nodes(errorRows[4], "details")[0].textContent, /15.125 с/);
  assert.match(actionText(errorRows[4]), /не отменяются/);
  assert.equal(nodes(errorsApp.elements.messages, "button").length, 0, "No mutation retry buttons");
  assert(nodes(errorsApp.elements.messages, "details").every(d => !d.open));

  const nullProgress = Object.assign(idle(), {slice: {slice_id: "pending", state: "running", progress: null, valid: false,
    observation: {state: "stopped", error: {message: "Нет чтения"}}}, export: {export_id: "pending-export", state: "queued", kind: "project"}});
  const nullApp = harness(path => ({code: 200, body: path === "/api/status" ? status : nullProgress}));
  await settle();
  assert.match(nullApp.elements["slice-state"].textContent, /Прогресс не сообщён/);
  assert.match(nullApp.elements["export-state"].textContent, /Прогресс не сообщён/);
  assert(!nullApp.elements["slice-state"].textContent.includes("0%"));
  assert.match(actionText(nullApp.elements["slice-state"]), /операция могла продолжиться/);
  assert(!actionText(nullApp.elements["slice-state"]).includes("pending"), "IDs and observer internals stay in details");
  assert.equal(nodes(nullApp.elements["slice-state"], "details")[0].open, false);
  assert.match(nullApp.elements["export-state"].textContent, /проекта 3MF/);

  const estimateSnapshot = toolSnapshot([{name: "get_slicing_result", data: finalSlice.result},
    {name: "get_slicing_result", data: {source: "ste_slicer", print_time_seconds: 0, material: {length_mm: 0, weight_g: null}}}]);
  estimateSnapshot.slice = finalSlice;
  const estimateApp = harness(path => ({code: 200, body: path === "/api/status" ? status : estimateSnapshot}));
  await settle();
  const estimateText = actionText(estimateApp.elements.messages);
  for (const phrase of ["1 ч 2 мин 3 с", "1270 мм", "3,6 г", "0 ч 0 мин 0 с", "длина 0 мм", "масса не сообщено"]) assert(estimateText.includes(phrase), phrase);
  assert(!actionText(estimateApp.elements["slice-state"]).includes("print_time_seconds"));
  assert.match(technicalNodes(estimateApp.elements.messages)[0].textContent, /print_time_seconds/);
  const settingsPage = toolSnapshot([{name: "describe_settings", data: {scope: "global", total: 120, offset: 20, limit: 7, returned: 7,
    next_offset: 27, truncated: true, settings: Array.from({length: 7}, (_, i) => ({key: "new-key-" + i,
      label: "Source label " + i, category: "support", description: "<img> description **literal**", value: 0.15, unit: "mm", available: true}))}}]);
  const pageApp = harness(path => ({code: 200, body: path === "/api/status" ? status : settingsPage}));
  await settle();
  assert.equal(pageApp.elements.messages.children.length, 1, "Metadata page remains one compact card");
  const pageText = actionText(pageApp.elements.messages);
  assert.match(pageText, /найдено: 120; получено на странице: 7/);
  assert.match(pageText, /Есть следующая страница/);
  assert.match(pageText, /Source label 0: 0,15 мм/);
  assert(!pageText.includes("Source label 6"));
  const pageDetails = technicalNodes(pageApp.elements.messages)[0];
  assert.equal(pageDetails.open, false);
  assert.match(pageDetails.textContent, /Source label 6/);
  assert.equal(nodes(pageApp.elements.messages, "img").length, 0);
  assert(!pageApp.requests.some(r => r.options && r.options.method === "POST" && !readOnlyRpc(r)), "UI does not fetch all pages through LLM");
  const savedA = Object.assign(idle(), {session_id: "archive-a", process_id: "epoch-one", title: "Кронштейн <img> " + "длинное".repeat(15), restored: true,
    persistence: {state: "saved", saved_revision: 3}, messages: [{message_id: "ha", role: "user", text: "Кронштейн PETG", historical: true,
      attachments: [{name: "photo.png", expired: true}], event_id: 1}], last_event_id: 1});
  const savedB = Object.assign(idle(), {session_id: "archive-b", process_id: "epoch-one", title: "Декор PLA", restored: true,
    persistence: {state: "saved", saved_revision: 2}, messages: [{message_id: "hb", role: "assistant", text: "Другой разговор", historical: true, event_id: 1}], last_event_id: 1});
  const historyStatus = Object.assign({}, status, {process_id: "epoch-one"});
  let savedChats = [savedA, savedB], creates = 0;
  const archive = harness((path, options) => {
    if (path === "/api/status") return {code: 200, body: historyStatus};
    if (path === "/api/sessions" && options && options.method === "POST") {
      creates++;
      const created = Object.assign(idle(), {session_id: "new-chat", title: "Новый диалог", process_id: "epoch-one"});
      savedChats.push(created);
      return {code: 201, body: created};
    }
    if (path === "/api/sessions") return {code: 200, body: {ready: true, persistent: true, sessions: savedChats}};
    const chat = savedChats.find(item => path === "/api/sessions/" + item.session_id);
    if (chat && options && options.method === "DELETE") {
      savedChats = savedChats.filter(item => item !== chat);
      return {code: 200, body: {session_id: chat.session_id, deleted: true}};
    }
    if (chat) return {code: 200, body: chat};
    throw Error("Unexpected history request " + path);
  }, new Map([["ste_ai_session_id", "archive-a"]]));
  await settle();
  assert.equal(archive.elements["chat-list"].children.length, 2);
  assert.match(archive.elements["chat-list"].children[0].className, /current/);
  assert.equal(nodes(archive.elements["chat-list"], "img").length, 0);
  assert.match(archive.elements.messages.textContent, /недоступно, загрузите снова/);
  chatOpen(archive, 1).emit("click");
  await settle();
  assert.equal(archive.storage.get("ste_ai_session_id"), "archive-b");
  assert.match(archive.elements.messages.textContent, /Другой разговор/);
  assert(!archive.elements.messages.textContent.includes("PETG"));
  assert(archive.elements["chat-input"].focused);
  chatDelete(archive, 1).emit("click");
  await settle();
  assert.equal(savedChats.length, 1);
  assert(!archive.storage.has("ste_ai_session_id"));
  archive.elements["btn-new-session"].emit("click");
  await settle();
  assert.equal(creates, 1);
  assert.equal(archive.storage.get("ste_ai_session_id"), "new-chat");
  assert(!archive.requests.some(r => r.path.endsWith("/messages")), "List/switch/delete/new never replays a message");

  let releaseOldPage;
  const switching = harness(async (path, options) => {
    if (path === "/api/status") return {code: 200, body: historyStatus};
    if (path === "/api/sessions") return {code: 200, body: {ready: true, persistent: true, sessions: [savedA, savedB]}};
    if (path.startsWith("/api/events")) {
      await new Promise(resolve => { releaseOldPage = resolve; });
      return {code: 200, body: {events: [{id: 2, type: "assistant_message", data: {message_id: "late-old", role: "assistant", text: "OLD PAGE"}}], next_after: 2, has_more: false}};
    }
    return {code: 200, body: path.endsWith("archive-b") ? savedB : savedA};
  }, new Map([["ste_ai_session_id", "archive-a"]]));
  await settle(); switching.tick(); await settle();
  chatOpen(switching, 1).emit("click");
  await settle(); releaseOldPage(); await settle();
  assert.match(switching.elements.messages.textContent, /Другой разговор/);
  assert(!switching.elements.messages.textContent.includes("OLD PAGE"), "Old generation cannot pollute selected conversation");

  const busyHistory = harness(path => ({code: 200, body: path === "/api/status" ? Object.assign({}, historyStatus, {agent_state: "processing"}) :
    path === "/api/sessions" ? {ready: true, persistent: true, sessions: [savedA, savedB]} : Object.assign({}, savedA, {operation_busy: true})}),
    new Map([["ste_ai_session_id", "archive-a"]]));
  await settle();
  assert(busyHistory.elements["btn-new-session"].disabled && nodes(busyHistory.elements["chat-list"], "button").every(button => button.disabled));
  busyHistory.elements["btn-new-session"].emit("click");
  chatOpen(busyHistory, 1).emit("click");
  chatDelete(busyHistory, 1).emit("click");
  await settle();
  assert(!busyHistory.requests.some(r => r.options && r.options.method !== "GET" && !readOnlyRpc(r)));
  assert.match(busyHistory.elements.messages.textContent, /PETG/, "Readonly snapshot still displayed during busy");

  const expiredStorage = new Map([["ste_ai_session_id", "archive-a"], ["ste_ai_pending", JSON.stringify({session_id: "archive-a", process_id: "old-process", text: "OLD PENDING", client_message_id: "old-id", attachment_ids: ["old-file"]})]]);
  const expiredPendingApp = harness(path => ({code: 200, body: path === "/api/status" ? historyStatus : path === "/api/sessions" ?
    {ready: true, persistent: true, sessions: [savedA]} : savedA}), expiredStorage);
  await settle();
  assert(!expiredStorage.has("ste_ai_pending"));
  assert.match(expiredPendingApp.elements.messages.textContent, /автоматически не повторялась/);
  assert(!expiredPendingApp.requests.some(r => r.options && r.options.method === "POST" && !readOnlyRpc(r)));

  const saveStateApp = harness(path => ({code: 200, body: path === "/api/status" ? historyStatus : path === "/api/sessions" ?
    {ready: true, persistent: true, sessions: [savedA]} : path.startsWith("/api/events") ?
      {events: [], next_after: 1, has_more: false, persistence: {state: "saved", saved_revision: 8}} :
      Object.assign({}, savedA, {history_truncated: true, persistence: {state: "saving", saved_revision: 7}})}),
    new Map([["ste_ai_session_id", "archive-a"]]));
  await settle();
  assert.match(saveStateApp.elements["save-state"].textContent, /Сохранение/);
  saveStateApp.tick(); await settle();
  assert.match(saveStateApp.elements["save-state"].textContent, /Сохранено на этом компьютере/);
  assert.match(saveStateApp.elements["save-state"].textContent, /последние сообщения/);

  let currentEpoch = "epoch-one";
  const reopenedTab = harness(path => ({code: 200, body: path === "/api/status" ? Object.assign({}, status, {process_id: currentEpoch}) :
    path === "/api/sessions" ? {ready: true, persistent: true, sessions: [savedA]} : path.startsWith("/api/events") ? {events: [], next_after: 1, has_more: false} :
      currentEpoch === "epoch-one" ? Object.assign({}, savedA, {messages: [{message_id: "live-old", role: "assistant", text: "Old live card", event_id: 1}]}) :
      Object.assign({}, savedA, {process_id: currentEpoch})}), new Map([["ste_ai_session_id", "archive-a"]]));
  await settle();
  assert.match(reopenedTab.elements.messages.textContent, /Old live card/);
  currentEpoch = "epoch-two";
  for (let i = 0; i < 10; i++) { reopenedTab.tick(); await settle(); }
  assert(!reopenedTab.elements.messages.textContent.includes("Old live card"));
  assert.match(reopenedTab.elements.messages.textContent, /Из сохранённой истории/);
  assert(!reopenedTab.requests.some(r => r.options && r.options.method === "POST" && !readOnlyRpc(r)));
  const historyErrors = harness((path, options) => {
    if (path === "/api/status") return {code: 200, body: historyStatus};
    if (path === "/api/sessions") return {code: 200, body: {ready: true, persistent: true, sessions: [savedA, savedB], error: "Повреждённый файл сохранён"}};
    if (path.endsWith("archive-b")) return {code: 503, body: {error: {message: "Чтение/удаление не подтверждено"}}};
    return {code: 200, body: savedA};
  }, new Map([["ste_ai_session_id", "archive-a"]]));
  await settle();
  assert.match(historyErrors.elements["history-status"].textContent, /Повреждённый файл сохранён/);
  chatOpen(historyErrors, 1).emit("click");
  await settle();
  assert.match(historyErrors.elements["history-status"].textContent, /не подтверждено/);
  assert.equal(historyErrors.storage.get("ste_ai_session_id"), "archive-a");
  chatDelete(historyErrors, 1).emit("click");
  await settle();
  assert.equal(historyErrors.requests.filter(r => r.options && r.options.method === "DELETE").length, 1, "Unknown deletion is not retried");
  assert.match(historyErrors.elements.messages.textContent, /PETG/, "Failed opening/deletion retains displayed conversation");
  assert(!historyErrors.requests.some(r => r.path.endsWith("/messages")));
  let pollingEpoch = "epoch-one";
  const epochPolling = harness(path => ({code: 200, body: path === "/api/status" ? historyStatus :
    path === "/api/sessions" ? {ready: true, persistent: true, sessions: [savedA]} : path.startsWith("/api/events") ?
      {process_id: pollingEpoch, events: [], next_after: 1, has_more: false} : pollingEpoch === "epoch-one" ?
        Object.assign({}, savedA, {messages: [{message_id: "old-poll", role: "assistant", text: "Old poll card", event_id: 1}]}) :
        Object.assign({}, savedA, {process_id: pollingEpoch})}), new Map([["ste_ai_session_id", "archive-a"]]));
  await settle();
  pollingEpoch = "epoch-two";
  epochPolling.tick(); await settle();
  assert(!epochPolling.elements.messages.textContent.includes("Old poll card"), "Polling epoch detects restart without waiting for status or cursor mismatch");
  assert.match(epochPolling.elements.messages.textContent, /Из сохранённой истории/);
  assert(!epochPolling.requests.some(r => r.options && r.options.method === "POST" && !readOnlyRpc(r)));
  // Scene panel: live IDs, JSON numbers, honest failures (MCP is faked; no STE).
  const rpcCalls = [];
  let sceneModels = [{object_id: "model-7", name: "bracket.stl", is_group: false, bounds: {size: {x: 10, y: 20, z: 30}, unit: "mm"}}];
  let rotateResult = {ok: false, error: {code: "model_transform_unsupported", message: "Закройте G-code preview"}};
  const rpcReply = value => ({code: 200, body: {jsonrpc: "2.0", id: 1, result: {content: [{type: "text", text: JSON.stringify(value)}], isError: !value.ok}}});
  const sceneApp = harness((path, options) => {
    if (path === "/api/status") return {code: 200, body: status};
    if (path === "/mcp") {
      const params = JSON.parse(options.body).params;
      rpcCalls.push(params);
      if (params.name === "list_models") return rpcReply({ok: true, data: {models: sceneModels}});
      if (params.name === "get_model_state") return rpcReply({ok: true, data: {object_id: params.arguments.object_id, position_mm: {x: 1, y: 0, z: 2}, outside_build_area: false}});
      if (params.name === "rotate_model") return rpcReply(rotateResult);
      if (params.name === "move_model") return rpcReply({ok: true, data: {object_id: "model-7", changed: true, warnings: [],
        before: {position_mm: {x: 1, y: 0, z: 2}}, after: {position_mm: {x: 0.12, y: 0, z: -3}, bounds: {size: {x: 10, y: 20, z: 30}, unit: "mm"}}}});
    }
    return {code: 200, body: idle()};
  });
  await settle();
  assert.match(sceneApp.elements["scene-status"].textContent, /Объектов в сцене: 1/);
  assert.equal(sceneApp.elements["move-x"].value, "1", "Position is prefilled from a live readback");
  field(sceneApp, "rot-axis").value = "z";
  field(sceneApp, "rot-angle").value = "";
  sceneApp.elements["btn-rotate"].emit("click"); await settle();
  assert(!rpcCalls.some(c => c.name === "rotate_model"), "Empty angle is not sent as 0");
  assert.match(sceneApp.elements["scene-result"].textContent, /поле пустое/);
  for (const bad of ["15deg", "1e400", "NaN", "true"]) {
    field(sceneApp, "rot-angle").value = bad;
    sceneApp.elements["btn-rotate"].emit("click"); await settle();
  }
  assert(!rpcCalls.some(c => c.name === "rotate_model"), "Units, non-finite and booleans are rejected locally");
  field(sceneApp, "rot-angle").value = "15";
  sceneApp.elements["btn-rotate"].emit("click"); await settle();
  const rotate = rpcCalls.find(c => c.name === "rotate_model");
  assert.deepEqual(rotate.arguments, {object_id: "model-7", axis: "z", angle_degrees: 15});
  assert.equal(typeof rotate.arguments.angle_degrees, "number");
  assert.equal(sceneApp.elements["scene-result"].className, "scene-result fail", "Backend refusal is never a green card");
  assert.match(sceneApp.elements["scene-result"].textContent, /не выполнено или не подтверждено.*preview/);
  sceneApp.elements["move-x"].value = "0,12"; field(sceneApp, "move-z").value = "-3";
  sceneApp.elements["btn-move"].emit("click"); await settle();
  const move = rpcCalls.find(c => c.name === "move_model");
  assert.deepEqual(move.arguments.position_mm, {x: 0.12, y: 0, z: -3});
  assert.equal(sceneApp.elements["scene-result"].className, "scene-result ok");
  assert.match(sceneApp.elements["scene-result"].textContent, /подтверждено чтением.*устаревшими/);
  sceneModels = [];
  sceneApp.elements["btn-scene-refresh"].emit("click"); await settle();
  assert.match(sceneApp.elements["scene-status"].textContent, /больше не существует/, "A vanished ID is dropped, never reused");
  assert(sceneApp.elements["btn-rotate"].disabled);
  const admissionSnapshot = Object.assign(idle(), {messages: [{message_id: "numeric-notice", role: "assistant", event_id: 2,
    text: "Высота слоя изменена на 0,12 мм."}],
    tool_events: [{id: 1, type: "tool_finished", data: {name: "set_setting", tool_call_id: "parsed", result: {ok: true,
      data: {key: "layer_height", before: 0.1, after: 0.12, unit: "mm"},
      argument_admission: {field: "value", from_type: "string", to_type: "number", reason: "strict_json_number_literal", stage: "llm_boundary"}}}}], last_event_id: 2});
  const admissionApp = harness(path => ({code: 200, body: path === "/api/status" ? status : admissionSnapshot}));
  await settle();
  assert.match(actionText(admissionApp.elements.messages), /разбор аргументов модели/, "One notice per group; raw admission stays in technical details");
  assert.match(actionText(admissionApp.elements.messages), /0,1 → 0,12/);
  assert.match(technicalNodes(admissionApp.elements.messages)[0].textContent, /strict_json_number_literal/);
  assert(!admissionApp.requests.some(r => r.options && r.options.method === "POST" && !readOnlyRpc(r)));
  const stagedSnapshot = toolSnapshot([
    {name: "rotate_model", result: {ok: false, error: {code: "model_transform_unsupported", message: "Выключите Automatic drop down в UI", stage: "preflight", operation_started: false}}},
    {name: "rotate_model", result: {ok: false, error: {code: "model_transform_not_applied", message: "Матрица не подтверждена", stage: "execution", operation_started: true}}}
  ]);
  const stagedApp = harness(path => ({code: 200, body: path === "/api/status" ? status : stagedSnapshot}));
  await settle();
  assert.match(actionText(toolRows(stagedApp)[0]), /отклонено до изменения модели/);
  assert.match(actionText(toolRows(stagedApp)[0]), /защиты не отключаются автоматически/);
  assert.match(actionText(toolRows(stagedApp)[1]), /Вызов штатной операции уже начат/);
  assert.equal(nodes(stagedApp.elements.messages, "button").length, 0);
  // Readback noise is rounded for geometry/estimates; unknown setting keys keep their key as label.
  const roundedApp = harness(path => ({code: 200, body: path === "/api/status" ? status : toolSnapshot([
    {name: "get_model_state", data: {object_id: "m", position_mm: {x: 3.4e-14, y: 29.497451782226562, z: -0.0000015}, bounds: {size: {x: 58.99745178222656, y: 25, z: 58.994903564453125}, unit: "mm"}}},
    {name: "get_slicing_result", data: {source: "ste_slicer", slice_id: "s", print_time_seconds: 17121, material: {length_mm: 10430.4, weight_g: 31.112081953125}}},
    {name: "get_setting", data: {key: "cylindrical_mode_base_diameter", value: 6, unit: "mm"}},
    {name: "get_setting", data: {key: "some_new_key", value: 0.123456, unit: "mm"}}
  ])}));
  await settle();
  const rounded = actionText(roundedApp.elements.messages);
  for (const phrase of ["0 / 29,5 / 0 мм", "59 / 25 / 58,99 мм", "длина 10430 мм; масса 31,1 г", "Диаметр 5D-основания: 6 мм", "some_new_key: 0,123456 мм"]) assert(rounded.includes(phrase), phrase);
  // One closed factual log per turn; repeated call IDs in another turn never collide.
  const groupedSnapshot = toolSnapshot(Array.from({length: 20}, (_, i) => ({name: "set_setting",
    data: {key: "infill_sparse_density", before: i, after: i + 1, unit: "%"}})));
  groupedSnapshot.tool_events.push({id: 41, type: "tool_finished", data: {turn_id: "next-turn", tool_call_id: "tool-0", name: "get_setting",
    result: {ok: true, data: {key: "layer_height", value: 0.12, unit: "mm"}}}});
  groupedSnapshot.last_event_id = 41;
  let groupPage = 0;
  const groupedApp = harness(path => {
    if (path === "/api/status") return {code: 200, body: status};
    if (path.startsWith("/api/events")) return {code: 200, body: {events: groupPage++ ? [] : [
      {id: 42, type: "tool_finished", data: groupedSnapshot.tool_events[39].data},
      {id: 43, type: "tool_started", data: groupedSnapshot.tool_events[38].data}
    ], next_after: 43, has_more: false}};
    return {code: 200, body: groupedSnapshot};
  });
  await settle();
  assert.equal(actionGroups(groupedApp).length, 2);
  assert.equal(toolRows(groupedApp).length, 21);
  assert(actionGroups(groupedApp).every(group => group.open === false));
  assert.match(visibleText(groupedApp.elements.messages), /20 завершено/);
  assert(!visibleText(groupedApp.elements.messages).includes("Заполнение:"), "Successful steps stay inside the collapsed log");
  actionGroups(groupedApp)[0].open = true;
  assert.match(visibleText(groupedApp.elements.messages), /0 → 1 %/);
  groupedApp.tick(); await settle();
  assert.equal(toolRows(groupedApp).length, 21, "Duplicate finish and late start update no extra step");
  assert.equal(toolRows(groupedApp)[19].className, "tool-card ok", "Late start cannot erase a received result");

  const mixedSnapshot = toolSnapshot([
    {name: "set_setting", data: {key: "layer_height", before: 0.1, after: 0.12, unit: "mm"}},
    {name: "set_setting", result: argumentFailure},
    {name: "rotate_model", result: {ok: false, error: {code: "qt_timeout", message: "Исход неизвестен"}}}
  ]);
  const mixedApp = harness(path => ({code: 200, body: path === "/api/status" ? status : mixedSnapshot}));
  await settle();
  assert.equal(actionGroups(mixedApp).length, 1);
  assert.match(visibleText(mixedApp.elements.messages), /1 завершено, 1 с ошибкой, 1 требует проверки/);
  assert.match(visibleText(mixedApp.elements.messages), /Отклонено до запуска/);
  assert.match(visibleText(mixedApp.elements.messages), /Исход операции требует проверки/);
  assert.match(visibleText(mixedApp.elements.messages), /Результат не подтверждён/);
  assert(mixedApp.elements.messages.children.filter(node => /action-warning/.test(node.className || "")).length === 2);
  assert(!visibleText(mixedApp.elements.messages).includes("0,1 → 0,12"), "A successful sibling does not hide the error or expand the log");

  const missingIds = Object.assign(idle(), {tool_events: [1, 2].map(id => ({id, type: "tool_finished",
    data: {tool_call_id: "reused", name: "get_setting", result: {ok: true, data: {key: "layer_height", value: id, unit: "mm"}}}})), last_event_id: 2});
  const missingApp = harness(path => ({code: 200, body: path === "/api/status" ? status : missingIds}));
  await settle();
  assert.equal(actionGroups(missingApp).length, 2, "No fabricated turn linkage without turn_id");
  assert.match(visibleText(missingApp.elements.messages), /ход не указан/);
  const startedOnly = Object.assign(idle(), {tool_events: [{id: 1, type: "tool_started", data: {turn_id: "interrupted", tool_call_id: "x", name: "set_setting", arguments: {}}}], last_event_id: 1});
  const incompleteApp = harness(path => ({code: 200, body: path === "/api/status" ? status : startedOnly}));
  await settle();
  assert.match(visibleText(incompleteApp.elements.messages), /Есть событие запуска без результата/);
  assert.match(visibleText(incompleteApp.elements.messages), /не повторяйте изменение вслепую/);
  assert(!visibleText(incompleteApp.elements.messages).includes("завершено"));

  const fileFeed = harness(path => ({code: 200, body: path === "/api/status" ? status : Object.assign(idle(), {artifacts: [downloadable, localOnly]})}));
  await settle();
  assert.equal(fileFeed.elements["file-message"].parentNode, fileFeed.elements.messages, "Files are a feed message, outside composer");
  assert.match(actionText(fileFeed.elements.artifacts), /123 Б/);
  assert(!actionText(fileFeed.elements.artifacts).includes("Нарезка: slice-1"), "Operation IDs are technical details only");
  assert.equal(nodes(fileFeed.elements.artifacts, "a").length, 1);

  const attachSnapshot = toolSnapshot([{name: "load_model_file", args: {file_id: "attached-model"},
    data: {file_id: "attached-model", objects: [{object_id: "model-current"}]}}]);
  attachSnapshot.messages = [{message_id: "with-five", role: "user", text: "Импортируй первую модель", event_id: 0,
    attachments: Array.from({length: 5}, (_, i) => ({file_id: i ? "file-" + i : "attached-model", name: "длинное".repeat(20) + i + ".stl", kind: "model", size_bytes: 1234}))}];
  const attachApp = harness(path => ({code: 200, body: path === "/api/status" ? status : attachSnapshot}));
  await settle();
  const attachments = nodes(attachApp.elements.messages, "div").filter(node => node.className === "message-attachment");
  assert.equal(attachments.length, 5);
  assert.match(attachments[0].textContent, /Импортировано в сцену/);
  assert.match(attachments[1].textContent, /в сцену не импортировано/);
  assert.equal(nodes(attachApp.elements.messages, "img").length, 0, "Never turn local paths/filenames into image src");

  let scrollPage = 0;
  const scrollApp = harness(path => {
    if (path === "/api/status") return {code: 200, body: status};
    if (path.startsWith("/api/events")) return {code: 200, body: {events: [{id: ++scrollPage, type: "assistant_message", data: {message_id: "scroll-" + scrollPage, role: "assistant", text: "Новый ответ"}}], next_after: scrollPage}};
    return {code: 200, body: idle()};
  });
  await settle();
  const feed = scrollApp.elements.messages;
  feed.scrollHeight = 2000; feed.clientHeight = 400; feed.scrollTop = 200; feed.emit("scroll");
  scrollApp.tick(); await settle();
  assert.equal(feed.scrollTop, 200, "Incoming events never jump a reader to the bottom");
  assert.equal(scrollApp.elements["btn-latest"].hidden, false);
  scrollApp.elements["btn-latest"].emit("click");
  assert.equal(feed.scrollTop, 2000);
  assert.equal(scrollApp.elements["btn-latest"].hidden, true);

  const composerApp = harness(path => ({code: 200, body: path === "/api/status" ? status : idle()}));
  await settle();
  const input = composerApp.elements["chat-input"];
  input.style = {}; input.scrollHeight = 20000; input.clientHeight = 160;
  input.value = "я".repeat(12000); input.emit("input");
  assert.equal(input.style.height, "160px");
  assert.equal(input.className, "scrolls");
  input.emit("keydown", {key: "Enter", isComposing: true});
  input.emit("keydown", {key: "Enter", keyCode: 229});
  input.emit("keydown", {key: "Enter", shiftKey: true});
  assert(!composerApp.requests.some(request => request.path.endsWith("/messages")), "IME and Shift+Enter never submit");
  composerApp.elements["btn-sidebar"].emit("click");
  assert.equal(composerApp.elements["btn-sidebar"].getAttribute("aria-expanded"), "true");
  composerApp.elements.sidebar.emit("keydown", {key: "Escape"});
  assert.equal(composerApp.elements["btn-sidebar"].getAttribute("aria-expanded"), "false");
  assert(composerApp.elements["btn-sidebar"].focused, "Closing the sidebar returns focus to its trigger");
  // Each trash button owns its row ID, independent from the displayed conversation.
  let remaining = [savedA, savedB], releaseDelete;
  const rowDelete = harness(async (path, options) => {
    if (path === "/api/status") return {code: 200, body: historyStatus};
    if (path === "/api/sessions") return {code: 200, body: {ready: true, persistent: true, sessions: remaining}};
    if (options && options.method === "DELETE") {
      await new Promise(resolve => { releaseDelete = resolve; });
      remaining = remaining.filter(chat => chat.session_id !== "archive-b");
      return {code: 200, body: {session_id: "archive-b", deleted: true}};
    }
    return {code: 200, body: savedA};
  }, new Map([["ste_ai_session_id", "archive-a"]]));
  await settle();
  const row = rowDelete.elements["chat-list"].children[1];
  assert.equal(row.tagName, "DIV");
  assert.equal(row.getAttribute("role"), "listitem");
  assert.equal(nodes(row, "button").length, 2);
  assert.equal(nodes(chatOpen(rowDelete, 1), "button").length, 1, "No nested buttons");
  assert.equal(chatDelete(rowDelete, 1).getAttribute("aria-label"), "Удалить диалог " + savedB.title);
  chatDelete(rowDelete, 1).emit("click"); await settle();
  assert.match(rowDelete.confirmations[0], /Декор PLA/);
  assert.match(rowDelete.elements.messages.textContent, /PETG/);
  assert.equal(rowDelete.elements["chat-list"].children.length, 2, "No optimistic removal before acknowledgement");
  assert(nodes(rowDelete.elements["chat-list"], "button").every(button => button.disabled), "Both buttons inside every row are disabled during DELETE");
  chatOpen(rowDelete, 1).emit("click"); chatDelete(rowDelete, 0).emit("click");
  await settle();
  assert.equal(rowDelete.requests.filter(r => r.options && r.options.method === "DELETE").length, 1);
  assert.equal(rowDelete.requests.find(r => r.options && r.options.method === "DELETE").path, "/api/sessions/archive-b");
  assert(!rowDelete.requests.some(r => r.path === "/api/sessions/archive-b" && (!r.options || !r.options.method)), "Trash never opens the target chat");
  releaseDelete(); await settle();
  assert.equal(rowDelete.storage.get("ste_ai_session_id"), "archive-a");
  assert.match(rowDelete.elements.messages.textContent, /PETG/);
  assert.equal(rowDelete.elements["chat-list"].children.length, 1);
  assert(nodes(rowDelete.elements["chat-list"], "button").every(button => !button.disabled));

  // Cancel confirms the named target without issuing DELETE or changing selection.
  rowDelete.confirmation.allow = false;
  const beforeCancel = rowDelete.requests.length;
  chatDelete(rowDelete, 0).emit("click"); await settle();
  assert.equal(rowDelete.requests.length, beforeCancel);
  assert(rowDelete.confirmations[1].includes(savedA.title), "Full title remains literal even with markup-like characters");
  assert.equal(rowDelete.storage.get("ste_ai_session_id"), "archive-a");
  assert.equal(rowDelete.elements["chat-list"].children.length, 1);

  // Failed, malformed and lost DELETE responses do not hide the active chat or retry.
  for (const response of [
    {code: 503, body: {error: {message: "Исход удаления неизвестен"}}},
    {code: 200, body: {session_id: "archive-b", deleted: true}},
    {code: 200, body: {session_id: "archive-a", deleted: false}},
    null
  ]) {
    const rejectedDelete = harness((path, options) => {
      if (path === "/api/status") return {code: 200, body: historyStatus};
      if (path === "/api/sessions") return {code: 200, body: {ready: true, persistent: true, sessions: [savedA, savedB]}};
      if (path.startsWith("/api/events")) return {code: 200, body: {events: [], next_after: 1}};
      if (options && options.method === "DELETE") { if (!response) throw Error("Связь потеряна"); return response; }
      return {code: 200, body: savedA};
    }, new Map([["ste_ai_session_id", "archive-a"]]));
    await settle(); chatDelete(rejectedDelete, 0).emit("click"); await settle();
    rejectedDelete.tick(); await settle();
    assert.equal(rejectedDelete.requests.filter(r => r.options && r.options.method === "DELETE").length, 1);
    assert.equal(rejectedDelete.elements["chat-list"].children.length, 2);
    assert.match(rejectedDelete.elements.messages.textContent, /PETG/);
    assert.equal(rejectedDelete.storage.get("ste_ai_session_id"), "archive-a");
    assert.equal(rejectedDelete.elements["history-status"].className, "hint history-error");
    assert(rejectedDelete.elements["history-status"].textContent.length > 0);
  }

  // Guards remain inside handlers as well as on nested DOM buttons.
  for (const mode of ["pending", "upload", "switching", "global", "session", "operation"]) {
    let releaseGuard;
    const guardedStore = new Map([["ste_ai_session_id", "archive-a"]]);
    if (mode === "pending") guardedStore.set("ste_ai_pending", JSON.stringify({session_id: "archive-a", process_id: "epoch-one", text: "Неизвестная отправка", client_message_id: "pending-one", attachment_ids: []}));
    const guarded = harness(async (path, options) => {
      if (path === "/api/status") return {code: 200, body: Object.assign({}, historyStatus, {agent_state: mode === "global" ? "processing" : "idle"})};
      if (path === "/api/sessions") return {code: 200, body: {ready: true, persistent: true, sessions: [savedA, savedB]}};
      if (mode === "upload" && path === "/api/files") { await new Promise(resolve => { releaseGuard = resolve; }); return {code: 201, body: {file_id: "upload-guard", name: "part.stl", kind: "model", size_bytes: 1}}; }
      if (mode === "switching" && path.endsWith("archive-b")) { await new Promise(resolve => { releaseGuard = resolve; }); return {code: 200, body: savedB}; }
      return {code: 200, body: Object.assign({}, savedA, {agent_state: mode === "session" ? "processing" : "idle", operation_busy: mode === "operation"})};
    }, guardedStore);
    await settle();
    if (mode === "upload") { guarded.elements["chat-files"].files = [{name: "part.stl", size: 1}]; guarded.elements["chat-files"].emit("change"); await settle(); }
    if (mode === "switching") { chatOpen(guarded, 1).emit("click"); await settle(); }
    assert(nodes(guarded.elements["chat-list"], "button").every(button => button.disabled), mode + " disables real row buttons");
    const before = guarded.requests.length;
    chatDelete(guarded, 1).emit("click"); chatOpen(guarded, 0).emit("click"); await settle();
    assert.equal(guarded.requests.length, before, mode + " blocks row handlers");
    assert.equal(guarded.confirmations.length, 0, mode + " does not even show a deletion prompt");
    if (releaseGuard) { releaseGuard(); await settle(); }
  }
  console.log("Agent web contract: 46 scenarios OK (42 preserved/adapted + row-owned deletion, cancellation, unconfirmed DELETE and all row-button guards; offline DOM).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
