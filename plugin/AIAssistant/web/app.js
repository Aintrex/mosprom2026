// Temporary client adapted from artem_backend b9c0190; API_CONTRACT routes.
(function () {
  var sessionId = null, after = 0, pending = null, polling = false, generation = 0;
  var globalBusy = false, sessionBusy = false, cards = {}, messages = {}, lastSlice = null, failures = 0;
  var elMessages = document.getElementById("messages"), elInput = document.getElementById("chat-input");
  var elSend = document.getElementById("btn-send"), elState = document.getElementById("agent-state-text");
  var elNew = document.getElementById("btn-new-session");
  var elFiles = document.getElementById("chat-files"), elAttachments = document.getElementById("attachment-list");
  var selected = [], uploading = false;
  var elArtifacts = document.getElementById("artifacts"), artifacts = Object.create(null);

  function read(key) {
    try { return localStorage.getItem(key); } catch (err) { return null; }
  }
  function save(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (err) { elState.textContent = "История вкладки не сохранится: localStorage недоступен."; }
  }
  sessionId = read("ste_ai_session_id");
  try { pending = JSON.parse(read("ste_ai_pending") || "null"); } catch (err) { pending = null; }

  function api(path, options) {
    return fetch(path, options).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) {
          var error = new Error(body.error && body.error.message || "Ошибка HTTP " + response.status);
          error.status = response.status;
          throw error;
        }
        return body;
      });
    });
  }
  function post(path, data) {
    return api(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
  }
  function buttons() {
    elSend.disabled = globalBusy || sessionBusy || !sessionId || uploading || selected.some(function (item) { return !!item.error; });
    elSend.textContent = pending ? "Повторить попытку" : "Отправить";
    elNew.disabled = globalBusy || sessionBusy || !!pending || uploading;
    elFiles.disabled = globalBusy || sessionBusy || !!pending || uploading || !sessionId;
  }
  function attachmentList() {
    while (elAttachments.firstChild) elAttachments.removeChild(elAttachments.firstChild);
    var entries = pending ? (pending.attachments || (pending.attachment_ids || []).map(function (id) { return {name: id}; })) : selected;
    entries.forEach(function (item) {
      var row = document.createElement("div");
      row.textContent = item.name + (item.error ? " — " + item.error : item.file_id || pending ? " — загружен" : " — загрузка...");
      if (!pending) {
        var remove = document.createElement("button");
        remove.type = "button"; remove.textContent = "Убрать"; remove.disabled = uploading;
        remove.addEventListener("click", function () {
          if (uploading || pending) return;
          selected = selected.filter(function (entry) { return entry !== item; });
          attachmentList(); buttons();
        });
        row.appendChild(remove);
      }
      elAttachments.appendChild(row);
    });
  }
  function chooseFiles() {
    if (elFiles.disabled) return;
    var files = Array.prototype.slice.call(elFiles.files || []);
    elFiles.value = "";
    if (selected.length + files.length > 5) { errorMessage("Допускается до 5 вложений."); return; }
    var imageBytes = selected.filter(function (item) { return item.kind === "image"; }).reduce(function (sum, item) { return sum + item.size_bytes; }, 0);
    var items = files.map(function (file) {
      var image = /\.(jpg|jpeg|png)$/i.test(file.name);
      imageBytes += image ? file.size : 0;
      return {name: file.name, size_bytes: file.size, kind: image ? "image" : "model", file: file};
    });
    if (imageBytes > 10 * 1024 * 1024) { errorMessage("Изображения сообщения превышают 10 MiB."); return; }
    if (!items.length) return;
    selected = selected.concat(items); uploading = true; attachmentList(); buttons();
    var chain = Promise.resolve();
    items.forEach(function (item) {
      chain = chain.then(function () {
        var form = new FormData(); form.append("file", item.file);
        return api("/api/files", {method: "POST", body: form}).then(function (metadata) {
          delete item.file;
          Object.keys(metadata).forEach(function (key) { item[key] = metadata[key]; });
        }).catch(function (err) { delete item.file; item.error = err.message; }).then(attachmentList);
      });
    });
    chain.then(function () { uploading = false; attachmentList(); buttons(); });
  }
  function errorMessage(text) {
    var div = document.createElement("div");
    div.className = "msg error"; div.textContent = text;
    elMessages.appendChild(div);
  }
  function renderMessage(message) {
    if (messages[message.message_id]) return;
    messages[message.message_id] = true;
    var div = document.createElement("div");
    div.className = "msg " + (message.is_error ? "error" : message.role);
    div.textContent = message.text;
    (message.attachments || []).forEach(function (item) { div.textContent += "\nВложение: " + item.name; });
    elMessages.appendChild(div);
  }
  function renderTool(kind, data) {
    var key = (data.turn_id || "") + ":" + data.tool_call_id;
    var item = cards[key];
    if (!item) {
      var card = document.createElement("div"), head = document.createElement("div"), body = document.createElement("div");
      card.className = "tool-card"; head.className = "tool-head"; body.className = "tool-body";
      card.appendChild(head); card.appendChild(body); elMessages.appendChild(card);
      item = cards[key] = {card: card, head: head, body: body, started: false};
    }
    if (kind === "tool_started") { item.arguments = data.arguments; item.started = true; }
    var finished = kind === "tool_finished";
    var error = finished && data.result && data.result.error;
    var rejected = !item.started && error && error.code === "invalid_tool_arguments" &&
      error.stage === "validation" && error.executed === false;
    item.card.className = "tool-card " + (finished ? (data.result.ok ? "ok" : "fail") : "");
    item.head.textContent = (rejected ? "Отклонено до запуска: " : finished ? "Результат/отказ: " : "Вызов: ") + data.name;
    item.body.textContent = (item.started ? "Аргументы: " + JSON.stringify(item.arguments) :
      rejected ? "Аргументы не переданы API: отказ проверки до запуска." : "Аргументы не опубликованы; запуск API не подтверждён этой карточкой.") +
      (finished ? "\nРезультат: " + JSON.stringify(data.result) : "");
  }
  function renderSlice(slice) {
    lastSlice = slice;
    var target = document.getElementById("slice-state");
    if (!slice) { target.textContent = "Снимков нарезки ещё нет."; return; }
    target.textContent = "Последний снимок: " + slice.slice_id + ", " + slice.state +
      (slice.progress === null || slice.progress === undefined ? "" : ", progress=" + slice.progress) +
      (slice.state === "completed" ? (slice.valid ? ", актуален в момент чтения" : ", УСТАРЕЛ / актуальность не подтверждена") : "") +
      (slice.error ? ", ошибка: " + slice.error.message : "") +
      ". " + observationText(slice.observation);
    if (slice.result) target.textContent += " Оценки STE: " + JSON.stringify(slice.result);
  }
  function observationText(observation) {
    if (!observation) return "Снимок из tools; автонаблюдение этого запуска не подтверждено.";
    if (observation.state === "watching") return "Свой запуск обновляется автоматически без запросов LLM; idle относится к ответу агента.";
    if (observation.state === "stopped") return "Наблюдение прервано: " + (observation.error && observation.error.message || "проверьте UI") + "; engine мог продолжиться.";
    return "Наблюдение завершено; дальнейшие ручные изменения проверяются при чтении/скачивании.";
  }
  function renderExport(value) {
    var target = document.getElementById("export-state");
    if (!value) { target.textContent = "Своих экспортов ещё нет."; return; }
    target.textContent = "Экспорт " + value.export_id + " (" + value.kind + "): " + value.state +
      (value.progress === null || value.progress === undefined ? "" : ", progress=" + value.progress) +
      (value.state === "completed" && !value.valid ? ", УСТАРЕЛ / актуальность не подтверждена" : "") +
      (value.error ? ", ошибка: " + value.error.message : "") + ". " + observationText(value.observation);
  }
  function renderArtifact(artifact) {
    if (!artifact || typeof artifact.artifact_id !== "string") return;
    var row = artifacts[artifact.artifact_id];
    if (!row) {
      row = document.createElement("div");
      artifacts[artifact.artifact_id] = row; elArtifacts.appendChild(row);
    }
    while (row.firstChild) row.removeChild(row.firstChild);
    row.textContent = artifact.file_name + " (" + artifact.kind + ", " + artifact.size_bytes + " байт) — " + artifact.slice_id;
    if (artifact.valid === true && typeof artifact.download_url === "string" &&
        /^\/api\/files\/[A-Za-z0-9_-]{1,128}$/.test(artifact.download_url)) {
      var link = document.createElement("a");
      link.textContent = " Скачать"; link.href = artifact.download_url;
      link.target = "_blank"; link.rel = "noopener";
      row.appendChild(link);
    } else row.textContent += artifact.valid === false ? " — устарел / недоступен" : " — локальный результат, скачивание не подтверждено";
    if (artifact.download_error) row.textContent += " — " + artifact.download_error.message;
  }
  function applyEvent(event) {
    var data = event.data || {};
    if (event.type === "agent_state") {
      sessionBusy = data.state === "processing";
      if (!sessionBusy) globalBusy = false;
      elState.textContent = sessionBusy ? "Агент выполняет запрос..." :
        lastSlice && (lastSlice.state === "queued" || lastSlice.state === "running") ?
        "Ответ завершён; native-нарезка может продолжаться." : "Агент ожидает запрос.";
    } else if (event.type === "assistant_message") renderMessage(data);
    else if (event.type === "tool_started" || event.type === "tool_finished") renderTool(event.type, data);
    else if (event.type === "slice_progress") renderSlice(data);
    else if (event.type === "slice_finished") renderSlice(data);
    else if (event.type === "export_progress") renderExport(data);
    else if (event.type === "artifact_ready") renderArtifact(data.artifact);
    else if (event.type === "error" && !data.related_id) errorMessage(data.message);
    buttons();
  }
  function adopt(snapshot) {
    while (elMessages.firstChild) elMessages.removeChild(elMessages.firstChild);
    cards = {}; messages = {};
    while (elArtifacts.firstChild) elArtifacts.removeChild(elArtifacts.firstChild);
    artifacts = Object.create(null);
    (snapshot.artifacts || []).forEach(renderArtifact);
    var timeline = snapshot.messages.map(function (message) {
      return {id: message.event_id || 0, message: message};
    }).concat((snapshot.tool_events || []).map(function (event) { return {id: event.id, event: event}; }));
    timeline.sort(function (a, b) { return a.id - b.id; });
    timeline.forEach(function (item) {
      if (item.message) renderMessage(item.message);
      else renderTool(item.event.type, item.event.data);
    });
    after = snapshot.last_event_id;
    sessionBusy = snapshot.agent_state === "processing";
    renderSlice(snapshot.slice);
    renderExport(snapshot.export);
    if (pending && pending.session_id === snapshot.session_id &&
        snapshot.messages.some(function (m) { return m.client_message_id === pending.client_message_id; })) {
      pending = null; selected = []; save("ste_ai_pending", null); elInput.value = "";
    }
    if (pending) {
      elInput.value = pending.text;
      elState.textContent = "Есть неподтверждённая отправка. Повтор использует прежний client_message_id.";
    } else elState.textContent = sessionBusy ? "Агент выполняет запрос..." : "Диалог восстановлен.";
    attachmentList(); buttons();
  }
  function restore() {
    var current = ++generation;
    return api("/api/sessions/" + encodeURIComponent(sessionId)).then(function (snapshot) {
      if (current === generation) adopt(snapshot);
    });
  }
  function newSession() {
    ++generation;
    return post("/api/sessions", {}).then(function (snapshot) {
      sessionId = snapshot.session_id; pending = null; selected = []; after = 0;
      save("ste_ai_session_id", sessionId); save("ste_ai_pending", null); adopt(snapshot);
    });
  }
  function loadStatus() {
    return api("/api/status").then(function (status) {
      globalBusy = status.agent_state === "processing";
      document.getElementById("status-badge").textContent = status.llm.configured ?
        "LLM настроена; связь не проверена" : "Нужны настройки LLM";
      document.getElementById("cfg-state").textContent = status.llm.has_key ? "Ключ сохранён в Preferences." : "Ключ не подтверждён.";
      document.getElementById("st-printer").textContent = status.slicer.machine_name || "STE недоступен / машина не выбрана";
      document.getElementById("st-mode").textContent = status.slicer.printing_mode || "—";
      buttons();
    });
  }
  function pollPage(current) {
    return api("/api/events?session_id=" + encodeURIComponent(sessionId) + "&after=" + after).then(function (page) {
      if (current !== generation) return;
      page.events.forEach(function (event) {
        if (event.id > after) { applyEvent(event); after = event.id; }
      });
      after = page.next_after;
      if (page.has_more) return pollPage(current);
    });
  }
  function poll() {
    if (!sessionId || polling) return;
    polling = true;
    pollPage(generation).then(function () { failures = 0; }).catch(function (err) {
      failures++;
      if (err.status === 410 || err.status === 400) return restore();
      if (err.status === 404) {
        // Unknown process history: create no turn, require an explicit send again.
        pending = null; save("ste_ai_pending", null);
        return newSession().then(function () { errorMessage("История процесса сброшена; запрос сам не повторялся."); });
      }
      elState.textContent = "Связь потеряна; повтор polling будет позже.";
    }).then(function () { polling = false; }, function () { polling = false; });
  }
  function send(text) {
    text = text.trim();
    if (!text || !sessionId || globalBusy || sessionBusy || uploading || selected.some(function (item) { return !!item.error; })) return;
    if (pending && (pending.text !== text || pending.session_id !== sessionId)) {
      errorMessage("Сначала проверьте/повторите прежнюю неподтверждённую попытку; новый ID автоматически не создаётся.");
      return;
    }
    if (!pending) pending = {session_id: sessionId, text: text,
      attachment_ids: selected.map(function (item) { return item.file_id; }),
      attachments: selected.map(function (item) { return {file_id: item.file_id, name: item.name}; }),
      client_message_id: "client_" + Date.now() + "_" + Math.random().toString(36).slice(2)};
    save("ste_ai_pending", JSON.stringify(pending));
    sessionBusy = true; attachmentList(); buttons();
    var attempt = pending;
    post("/api/sessions/" + encodeURIComponent(sessionId) + "/messages",
      {text: attempt.text, attachment_ids: attempt.attachment_ids || [], client_message_id: attempt.client_message_id}).then(function () {
        // Read the accepted user message; never add a second optimistic copy.
        pending = null; selected = []; save("ste_ai_pending", null); elInput.value = "";
        return restore();
      }).catch(function (err) {
        sessionBusy = false;
        if (err.status >= 400 && err.status < 500 && err.status !== 409) {
          pending = null; save("ste_ai_pending", null); // Definitive HTTP rejection, no turn accepted.
        }
        errorMessage(err.message);
        attachmentList(); buttons();
        return restore().catch(function () {});
      });
  }

  document.getElementById("chat-form").addEventListener("submit", function (event) { event.preventDefault(); send(elInput.value); });
  elFiles.addEventListener("change", chooseFiles);
  elNew.addEventListener("click", function () { newSession().catch(function (err) { errorMessage(err.message); }); });
  var chips = document.querySelectorAll(".chip");
  for (var i = 0; i < chips.length; i++) {
    chips[i].addEventListener("click", function () { elInput.value = this.getAttribute("data-prompt"); send(elInput.value); });
  }
  loadStatus().then(function () {
    return sessionId ? restore().catch(function (err) {
      if (err.status === 404) return newSession().then(function () {
        errorMessage("История процесса сброшена. Проверьте проект; прежние изменения автоматически не повторялись.");
      });
      throw err;
    }) : newSession();
  }).catch(function (err) { errorMessage(err.message); });
  var ticks = 0;
  setInterval(function () {
    ticks++;
    if (failures && ticks % Math.min(10, failures + 1)) return;
    poll();
    if (ticks % 10 === 0) loadStatus().catch(function () {});
  }, 1000);
})();
