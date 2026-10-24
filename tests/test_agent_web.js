// Contract behavior in a minimal DOM, no browser/network/provider.
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("plugin/AIAssistant/web/app.js", "utf8");
class Element {
  constructor(tag = "div") { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this._text = ""; this.value = ""; this.open = false; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  appendChild(el) { this.children.push(el); }
  removeChild(el) { this.children.splice(this.children.indexOf(el), 1); }
  get firstChild() { return this.children[0]; }
  set innerHTML(value) { throw Error("Untrusted HTML insertion"); }
  addEventListener(kind, fn) { this.listeners[kind] = fn; }
  emit(kind) { this.listeners[kind]({preventDefault() {}}); }
  focus() { this.focused = true; }
}
function nodes(root, tag) { return (root.tagName === tag.toUpperCase() ? [root] : []).concat(root.children.flatMap(child => nodes(child, tag))); }
function visibleText(root) { return root.tagName === "DETAILS" ? "" : root._text + root.children.map(visibleText).join(" "); }
const idle = () => ({session_id: "session-test", messages: [], tool_events: [], agent_state: "idle", last_event_id: 0, slice: null});
const status = {llm: {configured: true, has_key: true}, agent_state: "idle", slicer: {machine_name: null, printing_mode: null}};
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
function harness(route, storage = new Map([["ste_ai_session_id", "session-test"]])) {
  const elements = {}, requests = [], timers = [];
  const context = {
    document: {getElementById(id) { return elements[id] || (elements[id] = new Element()); },
      createElement(tag) { return new Element(tag); }, createTextNode(text) { const node = new Element("#text"); node.textContent = text; return node; }, querySelectorAll() { return []; }},
    localStorage: {getItem: key => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    fetch: async (path, options) => {
      requests.push({path, options});
      const result = await route(path, options);
      return {ok: result.code < 400, status: result.code, json: async () => result.body};
    }, FormData: class { constructor() { this.parts = []; } append(key, value) { this.parts.push([key, value]); } },
    setInterval(fn) { timers.push(fn); }, confirm() { return true; }, console, URL
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
  assert(withFiles.elements["attachment-list"].children[0].textContent.startsWith("<photo>.png — загружен"));
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
  assert(fileRetry.elements["attachment-list"].children[0].textContent.startsWith("<photo>.png — загружен"));
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
  assert.equal(nodes(rows[1], "a").length, 0, "Historical local artifact is not a fake download");
  assert.equal(nodes(rows[2], "a").length, 0, "Unsafe href is never assigned");
  artifactApp.tick(); await settle();
  assert.equal(rows.length, 3, "Repeated artifact ID updates the same card");
  artifactApp.tick(); await settle();
  assert.equal(nodes(rows[0], "a").length, 0, "Known stale event removes the link");
  assert.match(rows[0].textContent, /устарел/);
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
  assert.match(stoppedApp.elements["slice-state"].textContent, /слайсер мог продолжить/);
  stoppedApp.tick(); await settle();
  assert.match(stoppedApp.elements["export-state"].textContent, /ожидает запуска/);
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
  assert.match(visibleText(rejectedRows[0].children[1]), /API слайсера не вызывался/);
  assert.match(visibleText(rejectedRows[0].children[1]), /числовому типу/);
  assert.match(nodes(rejectedRows[0], "details")[0].textContent, /float/);
  assert.equal(nodes(rejectedRows[0], "details")[0].open, false);
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
  assert.match(visibleText(correctedRows[1]), /подтверждённое значение 0,12/);
  assert.match(nodes(correctedRows[1], "details")[0].textContent, /"value": 0.12/);
  assert.equal(correctedRows[1].className, "tool-card ok");
  assert(!correctionApp.requests.some(r => r.options && r.options.method === "POST"), "UI never retries the rejected mutation");
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
  assert(hostileApp.requests.every(r => r.path.startsWith("/api/")), "Markdown images trigger no external fetches");

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
  const readable = visibleText(readableApp.elements.messages);
  for (const phrase of ["Высота слоя: 0,1 мм", "0,1 → 0,12 мм", "Модель загружена: **literal** <img>.stl", "Объектов: 1", "X / Y / Z: 10 / 20 / 30 мм", "Принтер: Hybrid 530", "5D Spiral", "210 °С", "50 мм/с", "0,123456 unknown-unit", "значение не изменилось"]) assert(readable.includes(phrase), phrase);
  assert(!readable.includes('"before"') && !readable.includes('"objects"'), "No JSON in primary tool summaries");
  assert.equal(nodes(readableApp.elements.messages, "strong").length, 0, "Filename emphasis stays literal");
  assert.equal(nodes(readableApp.elements.messages, "img").length, 0);
  assert.equal(nodes(readableApp.elements.messages, "details").length, 9);
  assert(nodes(readableApp.elements.messages, "details").every(d => d.open === false));
  assert.match(nodes(readableApp.elements.messages, "details")[1].textContent, /"after": 0.12/);

  const unknownSnapshot = toolSnapshot([
    {name: "unknown_future_tool", data: {setting: "not verified", html: "<img onerror=x>"}},
    {name: "set_setting", data: {unexpected: true}},
    {name: "set_setting", data: {key: "layer_height", after: {bad: "object"}}},
    {name: "constructor", data: {future: true}},
    {name: "get_slicer_state", data: {connected: true, machine: {name: "Hybrid 530"}, printing_mode: "classic", backend: {id: "CLIParserBackend", matches_mode: false}}}
  ]);
  const unknownApp = harness(path => ({code: 200, body: path === "/api/status" ? status : unknownSnapshot}));
  await settle();
  assert(unknownApp.elements.messages.children.every(row => row.className === "tool-card neutral"));
  assert.match(visibleText(unknownApp.elements.messages), /формат результата не распознан/);
  assert(!visibleText(unknownApp.elements.messages).includes("изменена"));
  assert.equal(nodes(unknownApp.elements.messages, "img").length, 0);
  assert.equal(unknownApp.elements.messages.children[3].children[0].textContent, "Инструмент", "Unknown names do not inherit dictionary labels");
  assert.match(visibleText(unknownApp.elements.messages.children[4]), /Движок не соответствует выбранному режиму/);

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
  const errorRows = errorsApp.elements.messages.children;
  assert.match(visibleText(errorRows[0]), /Исход операции требует проверки/);
  assert.match(visibleText(errorRows[0]), /неизвестен/);
  assert(!visibleText(errorRows[0]).includes("API слайсера не вызывался"));
  assert.match(visibleText(errorRows[1]), /Результат неактуален/);
  assert.equal(errorRows[2].className, "tool-card fail");
  assert.match(visibleText(errorRows[2]), /Ошибка движка/);
  assert.equal(errorRows[3].className, "tool-card neutral");
  assert.match(visibleText(errorRows[3]), /УСТАРЕЛ/);
  assert.match(visibleText(errorRows[4]), /Ответ языковой модели не получен вовремя/);
  assert(!visibleText(errorRows[4]).includes("15.125"), "Raw timing stays in closed details");
  assert.match(nodes(errorRows[4], "details")[0].textContent, /15.125 с/);
  assert.match(visibleText(errorRows[4]), /не отменяются/);
  assert.equal(nodes(errorsApp.elements.messages, "button").length, 0, "No mutation retry buttons");
  assert(nodes(errorsApp.elements.messages, "details").every(d => !d.open));

  const nullProgress = Object.assign(idle(), {slice: {slice_id: "pending", state: "running", progress: null, valid: false,
    observation: {state: "stopped", error: {message: "Нет чтения"}}}, export: {export_id: "pending-export", state: "queued", kind: "project"}});
  const nullApp = harness(path => ({code: 200, body: path === "/api/status" ? status : nullProgress}));
  await settle();
  assert.match(nullApp.elements["slice-state"].textContent, /Прогресс не сообщён/);
  assert.match(nullApp.elements["export-state"].textContent, /Прогресс не сообщён/);
  assert(!nullApp.elements["slice-state"].textContent.includes("0%"));
  assert.match(visibleText(nullApp.elements["slice-state"]), /операция могла продолжиться/);
  assert(!visibleText(nullApp.elements["slice-state"]).includes("pending"), "IDs and observer internals stay in details");
  assert.equal(nodes(nullApp.elements["slice-state"], "details")[0].open, false);
  assert.match(nullApp.elements["export-state"].textContent, /проекта 3MF/);

  const estimateSnapshot = toolSnapshot([{name: "get_slicing_result", data: finalSlice.result},
    {name: "get_slicing_result", data: {source: "ste_slicer", print_time_seconds: 0, material: {length_mm: 0, weight_g: null}}}]);
  estimateSnapshot.slice = finalSlice;
  const estimateApp = harness(path => ({code: 200, body: path === "/api/status" ? status : estimateSnapshot}));
  await settle();
  const estimateText = visibleText(estimateApp.elements.messages);
  for (const phrase of ["1 ч 2 мин 3 с", "1270 мм", "3,6 г", "0 ч 0 мин 0 с", "длина 0 мм", "масса не сообщено"]) assert(estimateText.includes(phrase), phrase);
  assert(!visibleText(estimateApp.elements["slice-state"]).includes("print_time_seconds"));
  assert.match(nodes(estimateApp.elements.messages, "details")[0].textContent, /print_time_seconds/);
  const settingsPage = toolSnapshot([{name: "describe_settings", data: {scope: "global", total: 120, offset: 20, limit: 7, returned: 7,
    next_offset: 27, truncated: true, settings: Array.from({length: 7}, (_, i) => ({key: "new-key-" + i,
      label: "Source label " + i, category: "support", description: "<img> description **literal**", value: 0.15, unit: "mm", available: true}))}}]);
  const pageApp = harness(path => ({code: 200, body: path === "/api/status" ? status : settingsPage}));
  await settle();
  assert.equal(pageApp.elements.messages.children.length, 1, "Metadata page remains one compact card");
  const pageText = visibleText(pageApp.elements.messages);
  assert.match(pageText, /найдено: 120; получено на странице: 7/);
  assert.match(pageText, /Есть следующая страница/);
  assert.match(pageText, /Source label 0: 0,15 мм/);
  assert(!pageText.includes("Source label 6"));
  const pageDetails = nodes(pageApp.elements.messages, "details")[0];
  assert.equal(pageDetails.open, false);
  assert.match(pageDetails.textContent, /Source label 6/);
  assert.equal(nodes(pageApp.elements.messages, "img").length, 0);
  assert(!pageApp.requests.some(r => r.options && r.options.method === "POST"), "UI does not fetch all pages through LLM");
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
      return {code: 200, body: {deleted: true}};
    }
    if (chat) return {code: 200, body: chat};
    throw Error("Unexpected history request " + path);
  }, new Map([["ste_ai_session_id", "archive-a"]]));
  await settle();
  assert.equal(archive.elements["chat-list"].children.length, 2);
  assert.match(archive.elements["chat-list"].children[0].className, /current/);
  assert.equal(nodes(archive.elements["chat-list"], "img").length, 0);
  assert.match(archive.elements.messages.textContent, /недоступно, загрузите снова/);
  archive.elements["chat-list"].children[1].emit("click");
  await settle();
  assert.equal(archive.storage.get("ste_ai_session_id"), "archive-b");
  assert.match(archive.elements.messages.textContent, /Другой разговор/);
  assert(!archive.elements.messages.textContent.includes("PETG"));
  assert(archive.elements["chat-input"].focused);
  archive.elements["btn-delete-session"].emit("click");
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
  switching.elements["chat-list"].children[1].emit("click");
  await settle(); releaseOldPage(); await settle();
  assert.match(switching.elements.messages.textContent, /Другой разговор/);
  assert(!switching.elements.messages.textContent.includes("OLD PAGE"), "Old generation cannot pollute selected conversation");

  const busyHistory = harness(path => ({code: 200, body: path === "/api/status" ? Object.assign({}, historyStatus, {agent_state: "processing"}) :
    path === "/api/sessions" ? {ready: true, persistent: true, sessions: [savedA, savedB]} : Object.assign({}, savedA, {operation_busy: true})}),
    new Map([["ste_ai_session_id", "archive-a"]]));
  await settle();
  assert(busyHistory.elements["btn-new-session"].disabled && busyHistory.elements["btn-delete-session"].disabled && busyHistory.elements["btn-open-session"].disabled);
  busyHistory.elements["btn-new-session"].emit("click");
  busyHistory.elements["chat-list"].children[1].emit("click");
  busyHistory.elements["btn-delete-session"].emit("click");
  await settle();
  assert(!busyHistory.requests.some(r => r.options && r.options.method !== "GET"));
  assert.match(busyHistory.elements.messages.textContent, /PETG/, "Readonly snapshot still displayed during busy");

  const expiredStorage = new Map([["ste_ai_session_id", "archive-a"], ["ste_ai_pending", JSON.stringify({session_id: "archive-a", process_id: "old-process", text: "OLD PENDING", client_message_id: "old-id", attachment_ids: ["old-file"]})]]);
  const expiredPendingApp = harness(path => ({code: 200, body: path === "/api/status" ? historyStatus : path === "/api/sessions" ?
    {ready: true, persistent: true, sessions: [savedA]} : savedA}), expiredStorage);
  await settle();
  assert(!expiredStorage.has("ste_ai_pending"));
  assert.match(expiredPendingApp.elements.messages.textContent, /автоматически не повторялась/);
  assert(!expiredPendingApp.requests.some(r => r.options && r.options.method === "POST"));

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
  assert(!reopenedTab.requests.some(r => r.options && r.options.method === "POST"));
  const historyErrors = harness((path, options) => {
    if (path === "/api/status") return {code: 200, body: historyStatus};
    if (path === "/api/sessions") return {code: 200, body: {ready: true, persistent: true, sessions: [savedA, savedB], error: "Повреждённый файл сохранён"}};
    if (path.endsWith("archive-b")) return {code: 503, body: {error: {message: "Чтение/удаление не подтверждено"}}};
    return {code: 200, body: savedA};
  }, new Map([["ste_ai_session_id", "archive-a"]]));
  await settle();
  assert.match(historyErrors.elements["history-status"].textContent, /Повреждённый файл сохранён/);
  historyErrors.elements["chat-list"].children[1].emit("click");
  await settle();
  assert.match(historyErrors.elements["history-status"].textContent, /не подтверждено/);
  assert.equal(historyErrors.storage.get("ste_ai_session_id"), "archive-a");
  historyErrors.elements["btn-delete-session"].emit("click");
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
  assert(!epochPolling.requests.some(r => r.options && r.options.method === "POST"));
  const admissionSnapshot = Object.assign(idle(), {messages: [{message_id: "numeric-notice", role: "assistant", event_id: 2,
    text: "Для value выполнен явный разбор числовой строки на границе LLM. Причина строкового типа не установлена; это не подтверждение записи."}],
    tool_events: [{id: 1, type: "tool_finished", data: {name: "set_setting", tool_call_id: "parsed", result: {ok: true,
      data: {key: "layer_height", before: 0.1, after: 0.12, unit: "mm"},
      argument_admission: {field: "value", from_type: "string", to_type: "number", reason: "strict_json_number_literal", stage: "llm_boundary"}}}}], last_event_id: 2});
  const admissionApp = harness(path => ({code: 200, body: path === "/api/status" ? status : admissionSnapshot}));
  await settle();
  assert.match(visibleText(admissionApp.elements.messages), /явный разбор числовой строки/);
  assert.match(visibleText(admissionApp.elements.messages), /Причина строкового типа не установлена/);
  assert.match(nodes(admissionApp.elements.messages, "details")[0].textContent, /strict_json_number_literal/);
  assert(!admissionApp.requests.some(r => r.options && r.options.method === "POST"));
  const stagedSnapshot = toolSnapshot([
    {name: "rotate_model", result: {ok: false, error: {code: "model_transform_unsupported", message: "Выключите Automatic drop down в UI", stage: "preflight", operation_started: false}}},
    {name: "rotate_model", result: {ok: false, error: {code: "model_transform_not_applied", message: "Матрица не подтверждена", stage: "execution", operation_started: true}}}
  ]);
  const stagedApp = harness(path => ({code: 200, body: path === "/api/status" ? status : stagedSnapshot}));
  await settle();
  assert.match(visibleText(stagedApp.elements.messages.children[0]), /отклонено до изменения модели/);
  assert.match(visibleText(stagedApp.elements.messages.children[0]), /защиты не отключаются автоматически/);
  assert.match(visibleText(stagedApp.elements.messages.children[1]), /Вызов штатной операции уже начат/);
  assert.equal(nodes(stagedApp.elements.messages, "button").length, 0);
  const release = {version: "0.1.1", release_id: "numeric-diag-r1", numeric_argument_policy: "strict-json-numeric-v1"};
  const releaseApp = harness(path => ({code: 200, body: path === "/api/status" ? Object.assign({}, status, {plugin: release}) : idle()}));
  await settle();
  assert.match(releaseApp.elements["plugin-version"].textContent, /0\.1\.1 · numeric-diag-r1/);
  assert.match(releaseApp.elements["plugin-version"].textContent, /strict-json-numeric-v1/);
  assert(!releaseApp.elements["plugin-version"].textContent.includes("исправлено"));
  assert(!releaseApp.elements["plugin-version"].className.includes("warn"));
  assert.match(app.elements["plugin-version"].textContent, /Версия сервера не определена — обновите плагин и перезапустите STE Slicer/);
  assert.match(app.elements["plugin-version"].className, /warn/);
  const mismatchVersion = harness(path => ({code: 200, body: path === "/api/status" ? Object.assign({}, status, {plugin: Object.assign({}, release, {release_id: "older-release"})}) : idle()}));
  await settle();
  assert.match(mismatchVersion.elements["plugin-version"].textContent, /Несовпадение выпусков/);
  assert.match(mismatchVersion.elements["plugin-version"].textContent, /older-release/);
  const mismatchPolicy = harness(path => ({code: 200, body: path === "/api/status" ? Object.assign({}, status, {plugin: Object.assign({}, release, {numeric_argument_policy: "unsupported"})}) : idle()}));
  await settle();
  assert.match(mismatchPolicy.elements["plugin-version"].textContent, /Несовпадение выпусков/);
  assert(!releaseApp.requests.some(r => r.options && r.options.method === "POST"));
  console.log("Agent web contract: 36 scenarios OK (32 preserved + release/legacy/mismatch markers; offline DOM).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
