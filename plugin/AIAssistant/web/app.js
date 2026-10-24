// Temporary client adapted from artem_backend b9c0190; API_CONTRACT routes.
(function () {
  var sessionId = null, after = 0, pending = null, polling = false, generation = 0;
  var globalBusy = false, sessionBusy = false, cards = {}, messages = {}, failures = 0;
  var elMessages = document.getElementById("messages"), elInput = document.getElementById("chat-input");
  var elSend = document.getElementById("btn-send"), elState = document.getElementById("agent-state-text");
  var elNew = document.getElementById("btn-new-session");
  var elFiles = document.getElementById("chat-files"), elAttachments = document.getElementById("attachment-list");
  var selected = [], uploading = false;
  var elArtifacts = document.getElementById("artifacts"), artifacts = Object.create(null);
  var elHistory = document.getElementById("chat-list"), elHistoryStatus = document.getElementById("history-status");
  var elOpen = document.getElementById("btn-open-session"), elDelete = document.getElementById("btn-delete-session");
  var historyEntries = [], historyChoice = null, archiveReady = true, switching = false, operationBusy = false;
  var processId = null, bootAttempted = false;
  var partialHistory = false, expiredPending = false;
  // Defaults mirror API_CONTRACT; /api/status replaces them with server limits.
  var limits = {files: 5, image: 5 * 1024 * 1024, model: 50 * 1024 * 1024, imagesTotal: 10 * 1024 * 1024,
    extensions: [".3mf", ".jpeg", ".jpg", ".obj", ".png", ".stl"]};
  var imported = Object.create(null), attachmentNotes = Object.create(null), rpcId = 0;
  var sceneModels = [], sceneChoice = null, sceneState = null, sceneBusy = false, sceneRead = false;

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
    var busy = globalBusy || sessionBusy || operationBusy || switching;
    elSend.disabled = busy || !sessionId || uploading || selected.some(function (item) { return !!item.error; });
    elSend.textContent = pending ? "Повторить попытку" : "Отправить";
    elNew.disabled = busy || !!pending || uploading || !archiveReady;
    elFiles.disabled = busy || !!pending || uploading || !sessionId;
    elOpen.disabled = busy || !!pending || uploading || !historyChoice;
    elDelete.disabled = elOpen.disabled;
    for (var index = 0; index < elHistory.children.length; index++) elHistory.children[index].disabled = busy || !!pending || uploading;
    sceneButtons();
  }
  function renderPersistence(value, partial) {
    if (!value) return;
    if (typeof partial === "boolean") partialHistory = partial;
    var text = value.state === "saved" ? "Сохранено на этом компьютере" : value.state === "saving" ? "Сохранение…" :
      value.state === "error" ? "Сохранение не подтверждено: " + (value.error || "ошибка хранилища") : "Временный диалог: после закрытия не сохранится";
    document.getElementById("save-state").textContent = text + (partialHistory ? ". Архив содержит только последние сообщения." : "");
  }
  function historyRows() {
    clear(elHistory);
    historyEntries.forEach(function (item) {
      var row = element("button", (item.session_id === sessionId ? "Текущий: " : "") + item.title +
        (item.updated_at ? " · " + item.updated_at.replace("T", " ").slice(0, 16) + " UTC" : ""),
        "history-entry" + (item.session_id === sessionId ? " current" : ""));
      row.title = item.title;
      row.addEventListener("click", function () {
        historyChoice = item.session_id;
        openSession(historyChoice).catch(function (err) { elHistoryStatus.textContent = err.message; });
      });
      elHistory.appendChild(row);
    });
    buttons();
  }
  function loadSessions() {
    return api("/api/sessions").then(function (listing) {
      if (!Array.isArray(listing.sessions)) return; // Older server: keep the current-session flow.
      archiveReady = listing.ready !== false;
      historyEntries = listing.sessions;
      if (!historyEntries.some(function (item) { return item.session_id === historyChoice; })) historyChoice = sessionId || (historyEntries[0] && historyEntries[0].session_id);
      elHistoryStatus.textContent = !archiveReady ? "Загрузка архива…" : listing.error ||
        (listing.persistent ? historyEntries.length ? "Локальная история. Сцена STE общая для всех чатов." : "Пока нет сохранённых диалогов." : "История только до закрытия процесса.");
      historyRows();
    });
  }
  function openSession(id) {
    if (!id || globalBusy || sessionBusy || operationBusy || switching || pending || uploading) return Promise.resolve();
    switching = true; buttons();
    var current = ++generation;
    return api("/api/sessions/" + encodeURIComponent(id)).then(function (snapshot) {
      if (current !== generation) return;
      sessionId = id; dropSelected(); elInput.value = "";
      save("ste_ai_session_id", sessionId); adopt(snapshot); historyRows();
      if (elInput.focus) elInput.focus();
    }).then(function () { switching = false; buttons(); }, function (err) { switching = false; buttons(); throw err; });
  }
  function deleteSession() {
    if (elDelete.disabled || typeof confirm !== "function" || !confirm("Удалить выбранный диалог с этого компьютера? Сцена STE не изменится.")) return;
    var deleted = historyChoice;
    switching = true; buttons();
    api("/api/sessions/" + encodeURIComponent(deleted), {method: "DELETE"}).then(function () {
      if (deleted === sessionId) {
        ++generation; sessionId = null; save("ste_ai_session_id", null); clear(elMessages); clear(elArtifacts);
        cards = {}; messages = {}; artifacts = Object.create(null); renderSlice(null); renderExport(null);
        document.getElementById("chat-title").textContent = "Выберите диалог или создайте новый";
        document.getElementById("save-state").textContent = "";
      }
      return loadSessions();
    }).catch(function (err) { elHistoryStatus.textContent = err.message; }).then(function () { switching = false; buttons(); });
  }
  function sizeText(bytes) {
    if (!number(bytes) || bytes < 0) return "размер не сообщён";
    if (bytes < 1024) return bytes + " Б";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1).replace(".", ",") + " КБ";
    return (bytes / 1024 / 1024).toFixed(1).replace(".", ",") + " МБ";
  }
  function extensionOf(name) { var match = /\.[^.\\/]+$/.exec(String(name || "")); return match ? match[0].toLowerCase() : ""; }
  function kindOf(name) { return /^\.(jpg|jpeg|png)$/.test(extensionOf(name)) ? "image" : "model"; }
  function formatText(item) {
    var ext = extensionOf(item.name).slice(1).toUpperCase() || "?";
    return (item.kind || kindOf(item.name)) === "image" ? "Фото " + ext : "3D-модель " + ext;
  }
  function revokePreview(item) {
    if (item && item.preview) { try { URL.revokeObjectURL(item.preview); } catch (err) { /* already released */ } delete item.preview; }
  }
  function dropSelected() { selected.forEach(revokePreview); selected = []; }
  function attachmentPhase(item) {
    if (item.error) return "Ошибка: " + item.error + " Уберите файл, чтобы отправить сообщение.";
    if (pending) return item.file_id ? "Отправляется вместе с сообщением…" : "Отправляется…";
    if (!item.file_id) return "Загружается…";
    return item.kind === "image" ? "Готово: прикреплено к сообщению (агент увидит фото)." :
      "Готово: прикреплено к сообщению. В сцену ещё не импортировано — агент импортирует по запросу.";
  }
  function attachmentList() {
    while (elAttachments.firstChild) elAttachments.removeChild(elAttachments.firstChild);
    var entries = pending ? (pending.attachments || (pending.attachment_ids || []).map(function (id) { return {name: id}; })) : selected;
    entries.forEach(function (item) {
      var row = element("div", undefined, "attachment-row" + (item.error ? " bad" : item.file_id ? " ready" : ""));
      if (item.preview) {
        var img = element("img", undefined, "thumb"); img.src = item.preview; img.alt = ""; row.appendChild(img);
      }
      var info = element("div", undefined, "attachment-info");
      info.appendChild(element("strong", item.name));
      info.appendChild(element("span", formatText(item) + (number(item.size_bytes) ? " · " + sizeText(item.size_bytes) : ""), "hint"));
      info.appendChild(element("span", attachmentPhase(item), "phase"));
      row.appendChild(info);
      if (!pending) {
        var remove = element("button", "Убрать", "btn small");
        remove.type = "button"; remove.disabled = uploading && !item.error && !item.file_id;
        remove.title = "Убрать из этого сообщения; файл на диске не удаляется";
        remove.addEventListener("click", function () {
          if (pending || (uploading && !item.error && !item.file_id)) return;
          revokePreview(item);
          selected = selected.filter(function (entry) { return entry !== item; });
          attachmentList(); buttons();
        });
        row.appendChild(remove);
      }
      elAttachments.appendChild(row);
    });
  }
  function rejectFile(file, kind) {
    var ext = extensionOf(file.name);
    if (limits.extensions.indexOf(ext) < 0) return "формат " + (ext || "без расширения") + " не поддерживается (нужны " + limits.extensions.join(", ") + ").";
    var maximum = kind === "image" ? limits.image : limits.model;
    if (file.size > maximum) return "файл " + sizeText(file.size) + " больше лимита " + sizeText(maximum) + ".";
    if (!file.size) return "файл пустой.";
    return null;
  }
  function chooseFiles() {
    if (elFiles.disabled) return;
    var files = Array.prototype.slice.call(elFiles.files || []);
    elFiles.value = "";
    if (selected.length + files.length > limits.files) { errorMessage("Допускается до " + limits.files + " вложений в одном сообщении."); return; }
    var imageBytes = selected.filter(function (item) { return item.kind === "image" && !item.error; }).reduce(function (sum, item) { return sum + item.size_bytes; }, 0);
    var items = files.map(function (file) {
      var kind = kindOf(file.name), item = {name: file.name, size_bytes: file.size, kind: kind, file: file};
      item.error = rejectFile(file, kind);
      if (!item.error && kind === "image") {
        imageBytes += file.size;
        if (imageBytes > limits.imagesTotal) item.error = "фото сообщения вместе больше " + sizeText(limits.imagesTotal) + ".";
      }
      if (!item.error && kind === "image") {
        try { item.preview = URL.createObjectURL(file); } catch (err) { item.preview = null; }
      }
      if (item.error) delete item.file;
      return item;
    });
    if (!items.length) return;
    selected = selected.concat(items);
    var uploads = items.filter(function (item) { return item.file; });
    uploading = uploads.length > 0; attachmentList(); buttons();
    var chain = Promise.resolve(), uploadEpoch = processId;
    uploads.forEach(function (item) {
      chain = chain.then(function () {
        if (uploadEpoch !== processId || selected.indexOf(item) < 0) { delete item.file; return; }
        var form = new FormData(); form.append("file", item.file);
        return api("/api/files", {method: "POST", body: form}).then(function (metadata) {
          delete item.file;
          // Server ID is authoritative: equal names never share or replace a file_id.
          Object.keys(metadata).forEach(function (key) { item[key] = metadata[key]; });
        }).catch(function (err) { delete item.file; item.error = err.message + " Повторная загрузка автоматически не выполняется."; }).then(attachmentList);
      });
    });
    chain.then(function () { uploading = false; attachmentList(); buttons(); });
  }
  function element(tag, text, className) {
    var node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); node.textContent = ""; }
  function textNode(parent, text) { parent.appendChild(document.createTextNode(text)); }
  function technical(parent, value) {
    var details = element("details", undefined, "technical-details");
    details.appendChild(element("summary", "Технические детали"));
    details.appendChild(element("pre", JSON.stringify(value, null, 2)));
    parent.appendChild(details); // Closed by default; never HTML.
  }
  function safeMarkdownLink(value) {
    if (!/^https?:\/\//i.test(value) || /[\s\\<>\x00-\x1f\x7f]/.test(value)) return null;
    try {
      var url = new URL(value);
      return (url.protocol === "http:" || url.protocol === "https:") && !url.username && !url.password ? url.href : null;
    } catch (err) { return null; }
  }
  function inlineMarkdown(parent, text, depth) {
    var pattern = /`[^`\n]+`|\*\*[^\n]+?\*\*|__[^\n]+?__|\*[^*\n]+\*|_[^_\n]+_|!?\[[^\]\n]*\]\([^\s)\n]*\)/g;
    var match, offset = 0;
    while ((match = pattern.exec(text))) {
      textNode(parent, text.slice(offset, match.index));
      var token = match[0], node = null;
      if (token[0] === "`") node = element("code", token.slice(1, -1));
      else if (token[0] === "!") textNode(parent, token); // No remote image requests.
      else if (token[0] === "[") {
        var parts = /^\[([^\]]*)\]\(([^)]*)\)$/.exec(token), href = safeMarkdownLink(parts[2]);
        if (href) {
          node = element("a", parts[1]); node.href = href; node.target = "_blank"; node.rel = "noopener noreferrer";
        } else textNode(parent, token);
      } else {
        var strong = token.slice(0, 2) === "**" || token.slice(0, 2) === "__", width = strong ? 2 : 1;
        node = element(strong ? "strong" : "em");
        if (depth < 3) inlineMarkdown(node, token.slice(width, -width), depth + 1);
        else node.textContent = token.slice(width, -width);
      }
      if (node) parent.appendChild(node);
      offset = pattern.lastIndex;
    }
    textNode(parent, text.slice(offset));
  }
  function markdown(parent, value) {
    var lines = String(value || "").replace(/\r\n?/g, "\n").split("\n"), paragraph = null, list = null;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (/^\s*```/.test(line)) {
        var codeLines = []; paragraph = null; list = null;
        while (++i < lines.length && !/^\s*```\s*$/.test(lines[i])) codeLines.push(lines[i]);
        var pre = element("pre"); pre.appendChild(element("code", codeLines.join("\n"))); parent.appendChild(pre);
      } else if (!line.trim()) { paragraph = null; list = null; }
      else {
        var bullet = /^\s*([-+*]|\d+\.)\s+(.+)$/.exec(line);
        if (bullet) {
          var tag = /\d/.test(bullet[1]) ? "ol" : "ul";
          if (!list || list.tagName.toLowerCase() !== tag) { list = element(tag); parent.appendChild(list); }
          var item = element("li"); inlineMarkdown(item, bullet[2], 0); list.appendChild(item); paragraph = null;
        } else {
          list = null;
          if (!paragraph) { paragraph = element("p"); parent.appendChild(paragraph); }
          else paragraph.appendChild(element("br"));
          inlineMarkdown(paragraph, line, 0);
        }
      }
    }
  }
  function number(value) { return typeof value === "number" && isFinite(value); }
  function labelFrom(labels, key) { return typeof key === "string" && Object.prototype.hasOwnProperty.call(labels, key) ? labels[key] : null; }
  function valueText(value) {
    if (value === true) return "включено";
    if (value === false) return "выключено";
    if (number(value)) return String(value).replace(".", ",");
    return typeof value === "string" ? value : "не сообщено";
  }
  function plainValue(value) { return typeof value === "string" || typeof value === "boolean" || number(value); }
  function unitText(unit) { return labelFrom({mm: "мм", "mm/s": "мм/с", "°C": "°С", g: "г", s: "с", m: "м"}, unit) || unit; }
  var settingNames = {layer_height: "Высота слоя", layer_height_0: "Высота первого слоя", line_width: "Ширина линии",
    wall_line_count: "Число линий стенки", top_bottom_thickness: "Толщина дна и крышки", infill_sparse_density: "Заполнение",
    infill_pattern: "Рисунок заполнения", support_enable: "Поддержки", support_type: "Область поддержек", support_angle: "Угол поддержек",
    material_print_temperature: "Температура сопла", material_bed_temperature: "Температура стола", speed_print: "Скорость печати",
    retraction_enable: "Откат", retraction_amount: "Длина отката", retraction_speed: "Скорость отката", cool_fan_enabled: "Обдув",
    cool_fan_speed: "Скорость вентилятора", adhesion_type: "Прилипание к столу"};
  var toolNames = {get_setting: "Чтение настройки", set_setting: "Изменение настройки", describe_settings: "Доступные настройки",
    get_slicer_state: "Состояние STE Slicer", set_print_mode: "Режим печати", list_models: "Объекты сцены",
    load_model: "Загрузка модели", load_model_file: "Загрузка вложенной модели", get_model_bounds: "Габариты модели",
    get_model_state: "Положение модели", move_model: "Перемещение модели", rotate_model: "Поворот модели", scale_model: "Масштаб модели",
    delete_model: "Удаление модели", arrange_models: "Расстановка моделей", get_arrangement_status: "Состояние расстановки",
    list_quality_profiles: "Профили качества", select_profile: "Выбор профиля", list_materials: "Материалы", select_material: "Выбор материала",
    list_custom_profiles: "Пользовательские профили", create_custom_profile: "Создание профиля", select_custom_profile: "Выбор пользовательского профиля",
    save_custom_profile: "Сохранение профиля", start_slicing: "Запуск нарезки", get_slicing_status: "Состояние нарезки",
    get_slicing_result: "Результат нарезки", export_gcode: "Экспорт G-code", save_project_3mf: "Сохранение проекта 3MF", get_export_status: "Состояние экспорта",
    wait_for_slicing: "Ожидание нарезки", wait_for_export: "Ожидание записи файла"};
  function modeText(mode) { return labelFrom({classic: "Классический (3D)", cylindrical: "5D Spiral (цилиндрический)", cylindrical_full: "Полный 5D"}, mode) || valueText(mode); }
  function stateText(state) { return labelFrom({queued: "Принято, ожидает запуска", running: "Выполняется", completed: "Завершено", failed: "Ошибка"}, state) || "Состояние не подтверждено"; }
  function progressText(progress) { return number(progress) && progress >= 0 && progress <= 1 ? "Прогресс: " + Math.round(progress * 100) + "%" : "Прогресс не сообщён"; }
  function boundsText(bounds) {
    var size = bounds && bounds.size;
    return size ? "Размеры по осям X / Y / Z: " + ["x", "y", "z"].map(function (axis) { return valueText(size[axis]); }).join(" / ") +
      (typeof bounds.unit === "string" ? " " + unitText(bounds.unit) : " (единица не сообщена)") : null;
  }
  function estimates(result) {
    var seconds = result.print_time_seconds, time = "не сообщено";
    if (number(seconds) && seconds >= 0) {
      time = Math.floor(seconds / 3600) + " ч " + Math.floor(seconds % 3600 / 60) + " мин " + valueText(seconds % 60) + " с";
    }
    var material = result.material || {};
    return ["Оценки STE Slicer. Время печати: " + time + ".",
      "Материал: длина " + valueText(material.length_mm) + (number(material.length_mm) ? " мм" : "") +
      "; масса " + valueText(material.weight_g) + (number(material.weight_g) ? " г" : "") + "."];
  }
  function problem(error, rejected) {
    error = error || {};
    var code = error.code || "", title = "Не удалось выполнить инструмент", next = "Проверьте результат и состояние в STE Slicer. Не повторяйте изменение автоматически.";
    var networkTimeout = code === "network_timeout" || /[сС]етевой (timeout|бюджет)/.test(error.message || "");
    if (rejected) { title = "Отклонено до запуска"; next = "Настройка/сцена этим вызовом не менялись: API слайсера не вызывался. Ожидаемые поля и типы — в технических деталях."; }
    else if (error.stage === "preflight" && error.operation_started === false) {
      title = "Преобразование отклонено до изменения модели";
      next = "Предварительная проверка не разрешила запуск штатной операции. Проверьте указанную причину и UI; защиты не отключаются автоматически.";
    }
    else if (error.stage === "execution" && error.operation_started === true) {
      title = "Результат преобразования не подтверждён";
      next = "Вызов штатной операции уже начат. Перечитайте матрицу/сцену и UI; не повторяйте и не отменяйте действие автоматически.";
    }
    else if (networkTimeout) title = "Ответ языковой модели не получен вовремя";
    else if (code === "slice_result_stale" || code === "artifact_stale" || code === "artifact_changed") title = "Результат неактуален";
    else if (code === "qt_timeout" || code === "execution_error" || code === "tool_result_unconfirmed" || /_timeout$/.test(code) || /_not_applied$/.test(code)) {
      title = "Исход операции требует проверки"; next = "Результат не подтверждён: сначала перечитайте состояние (список моделей / настройку), не повторяйте действие вслепую.";
    }
    else if (code === "observation_interrupted") { title = "Наблюдение прервано"; next = "Слайсер мог продолжить работу. Прочитайте статус по ID или проверьте UI, не запускайте операцию повторно."; }
    else if (code === "tool_not_executed") title = "Вызов пропущен";
    else if (/_unsupported$|_not_found$|^auto_slicing_enabled$|^empty_scene$|^agent_busy$/.test(code)) {
      title = "Операция не разрешена в текущем состоянии"; next = "Проверка до запуска: STE Slicer не менялся. Устраните причину и повторите вручную.";
    }
    else if (/_busy$/.test(code) || code === "switch_in_progress") title = "Слайсер занят другой операцией";
    else if (/_failed$/.test(code)) title = "Слайсер сообщил об ошибке";
    var reason = typeof error.message === "string" ? error.message : "Причина не сообщена.";
    if (networkTimeout) reason = "Время ожидания ответа истекло. Уже выполненные инструменты и работа слайсера этим не отменяются.";
    if (rejected) reason = reason.replace("типу float", "числовому типу").replace("типу int", "целому числу")
      .replace("типу bool", "логическому типу").replace("типу enum", "значению из списка");
    return {title: title, lines: [reason, next]};
  }
  function paragraphs(parent, lines) { lines.forEach(function (line) { if (line) parent.appendChild(element("p", line)); }); }
  function errorMessage(text) {
    var div = element("div", undefined, "msg error"), info = problem({message: text});
    div.appendChild(element("strong", info.title)); paragraphs(div, info.lines); technical(div, {message: text}); elMessages.appendChild(div);
  }
  function renderMessage(message) {
    if (messages[message.message_id]) return;
    messages[message.message_id] = true;
    var div = document.createElement("div");
    div.className = "msg " + (message.is_error ? "error" : message.role);
    if (message.is_error) {
      var info = problem({message: message.text}); div.appendChild(element("strong", info.title)); paragraphs(div, info.lines);
      technical(div, {message: message.text, message_id: message.message_id});
    } else if (message.role === "assistant") markdown(div, message.text);
    else div.textContent = message.text;
    if (message.historical) div.appendChild(element("p", "Из сохранённой истории; состояние STE нужно перечитать.", "attachment-name"));
    (message.attachments || []).forEach(function (item) {
      var note = element("p", undefined, "attachment-name");
      if (item.file_id && !item.expired) (attachmentNotes[item.file_id] = attachmentNotes[item.file_id] || []).push({node: note, item: item});
      attachmentNote(note, item);
      div.appendChild(note);
    });
    elMessages.appendChild(div);
  }
  function attachmentNote(node, item) {
    var text = "Вложение: " + item.name + " (" + formatText(item) + ")";
    if (item.expired) text += " — недоступно, загрузите снова (вложения не переживают перезапуск)";
    else if ((item.kind || kindOf(item.name)) === "model") text += imported[item.file_id] ? " — импортировано в сцену" : " — прикреплено, в сцену не импортировано";
    node.textContent = text;
  }
  function markImported(fileId) {
    if (typeof fileId !== "string" || imported[fileId]) return;
    imported[fileId] = true;
    (attachmentNotes[fileId] || []).forEach(function (entry) { attachmentNote(entry.node, entry.item); });
  }
  function toolSummary(name, data, args) {
    name = labelFrom({wait_for_slicing: "get_slicing_status", wait_for_export: "get_export_status"}, name) || name;
    if (!data || typeof data !== "object") return null;
    args = args || {};
    var key = data.key || args.key, label = labelFrom(settingNames, key) || "Настройка", unit = typeof data.unit === "string" ? " " + unitText(data.unit) : " (единица не сообщена)";
    if (name === "get_setting" && plainValue(data.value)) return [label + ": " + valueText(data.value) + unit];
    if (name === "set_setting" && plainValue(data.after)) {
      return [label + (plainValue(data.before) ? ": " + valueText(data.before) + " → " : ": подтверждённое значение ") + valueText(data.after) + unit +
        (plainValue(data.before) ? data.before === data.after ? " (значение не изменилось)" : " (подтверждено чтением)" : "")];
    }
    if (name === "get_slicer_state" && data.connected === true) return [data.machine && data.machine.name ? "Принтер: " + data.machine.name : "Принтер не выбран.",
      "Режим: " + modeText(data.printing_mode), data.backend && data.backend.id ? "Движок: " + data.backend.id : "Движок не сообщён.",
      data.backend && data.backend.matches_mode === false ? "Движок не соответствует выбранному режиму. Проверьте состояние в STE Slicer." : null];
    if (name === "get_model_bounds" && boundsText(data.bounds)) return [boundsText(data.bounds), data.object_id ? "ID объекта: " + data.object_id : null];
    if ((name === "load_model" || name === "load_model_file") && Array.isArray(data.objects) && data.objects.length) {
      return ["Модель загружена" + (data.file_name ? ": " + data.file_name : "") + ". Объектов: " + data.objects.length + "."].concat(data.objects.map(function (model) {
        return "ID: " + valueText(model.object_id) + (boundsText(model.bounds) ? ". " + boundsText(model.bounds) : "");
      }));
    }
    if (name === "list_models" && Array.isArray(data.models)) return ["Объектов на сцене: " + data.models.length + "."].concat(data.models.map(function (model) {
      return valueText(model.name) + " — " + valueText(model.object_id);
    }));
    if (name === "describe_settings" && Array.isArray(data.settings)) return [number(data.total) ?
      "Параметров найдено: " + data.total + "; получено на странице: " + data.settings.length + "." : "Получены параметры текущего стека: " + data.settings.length + ".",
      data.next_offset !== null && number(data.next_offset) ? "Есть следующая страница. Не весь каталог показан; полный ответ страницы — в деталях." : null]
      .concat(data.settings.slice(0, 6).map(function (setting) { return (labelFrom(settingNames, setting.key) || valueText(setting.label || setting.key)) + ": " + valueText(setting.value) +
        (typeof setting.unit === "string" ? " " + unitText(setting.unit) : "") + (setting.available === false ? " — недоступна" : setting.settable === false ? " — только чтение" : ""); }))
      .concat(data.settings.length > 6 ? ["Остальные параметры страницы и подробные описания — в технических деталях."] : []);
    if (name === "get_slicing_result" && data.source === "ste_slicer") return estimates(data).concat(["ID нарезки: " + valueText(data.slice_id)]);
    if ((name === "start_slicing" || name === "get_slicing_status") && data.slice_id && data.state) return ["Нарезка: " + stateText(data.state) + ". " + progressText(data.progress),
      "ID: " + data.slice_id, data.state === "completed" && !data.valid ? "УСТАРЕЛ / актуальность не подтверждена." : null,
      data.state === "queued" || data.state === "running" ? "Запуск принят; завершение ещё не подтверждено." : null,
      data.error && data.error.message];
    if ((name === "export_gcode" || name === "save_project_3mf" || name === "get_export_status") && data.export_id && data.state) return [
      "Экспорт: " + stateText(data.state) + ". " + progressText(data.progress), "ID: " + data.export_id,
      data.state === "completed" && !data.valid ? "Результат устарел / актуальность не подтверждена." :
      data.state === "completed" && data.artifact ? "Подтверждён файл: " + valueText(data.artifact.file_name) : "Готовый файл пока не подтверждён.", data.error && data.error.message];
    if (name === "set_print_mode" && typeof data.after === "string") return ["Режим: " + modeText(data.after)];
    if (name === "select_profile" && data.after && typeof data.after.quality_type === "string") return ["Профиль качества: " + data.after.quality_type];
    if (name === "select_material" && data.after && data.after.material_id) return ["Материал экструдера " + valueText(data.extruder_id) + ": " + valueText(data.after.material_id)];
    if (/custom_profile$/.test(name) && data.profile_id) return ["ID профиля: " + data.profile_id, data.name ? "Имя: " + data.name : null,
      data.persistence ? "Проверка памяти: " + (data.persistence.memory_verified === true ? "подтверждена" : "не подтверждена") +
        "; файлов: " + (data.persistence.disk_verified === true ? "подтверждена" : "не подтверждена") + "; восстановление после перезапуска требует отдельной проверки." : null];
    if (name === "list_custom_profiles" && Array.isArray(data.profiles)) return ["Пользовательских профилей: " + data.profiles.length];
    if (name === "list_quality_profiles" && Array.isArray(data.profiles)) return ["Профилей качества: " + data.profiles.length];
    if (name === "list_materials" && Array.isArray(data.materials)) return ["Материалов: " + data.materials.length];
    if (/^(move|rotate|scale)_model$/.test(name) && data.object_id && typeof data.changed === "boolean") return transformLines(data);
    if (name === "delete_model" && data.removed === true) return ["Модель удалена из сцены (исходный файл не тронут). Отмена: Ctrl+Z в STE Slicer."];
    if (name === "get_model_state" && data.object_id && data.position_mm) return ["ID объекта: " + data.object_id,
      "Позиция X / Y / Z: " + ["x", "y", "z"].map(function (axis) { return valueText(data.position_mm[axis]); }).join(" / ") + " мм", boundsText(data.bounds)];
    if ((name === "arrange_models" || name === "get_arrangement_status") && data.arrangement_id && data.state) return ["Расстановка: " + stateText(data.state),
      "ID: " + data.arrangement_id, data.all_fit === false ? "Есть выход за границы стола." : "Полное размещение не подтверждено."];
    return null;
  }
  function positionText(position) {
    return position ? ["x", "y", "z"].map(function (axis) { return valueText(position[axis]); }).join(" / ") + " мм" : "не сообщена";
  }
  function transformLines(data) {
    var before = data.before || {}, after = data.after || {};
    return [data.changed ? "Преобразование подтверждено чтением сцены." : "Значение уже было таким; сцена не менялась.",
      "Позиция X / Y↑ / Z: " + positionText(before.position_mm) + (data.changed ? " → " + positionText(after.position_mm) : ""),
      after.bounds && boundsText(after.bounds)].concat(Array.isArray(data.warnings) ? data.warnings : []);
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
    var result = data.result || {}, error = finished && result.error;
    var rejected = !item.started && error && error.code === "invalid_tool_arguments" &&
      error.stage === "validation" && error.executed === false;
    var lines = finished && result.ok === true ? toolSummary(data.name, result.data, item.arguments) : null;
    var failedState = result.data && result.data.state === "failed";
    var unsettled = result.data && (result.data.state && (result.data.state !== "completed" || result.data.valid !== true) ||
      result.data.backend && result.data.backend.matches_mode === false);
    item.card.className = "tool-card " + (finished && (result.ok === false || failedState) ? "fail" : lines && !unsettled ? "ok" : "neutral");
    item.head.textContent = (labelFrom(toolNames, data.name) || "Инструмент") + (finished ? "" : " — выполняется");
    clear(item.body);
    while (item.card.children.length > 2) item.card.removeChild(item.card.children[2]);
    if (finished && result.ok === false) {
      var info = problem(error, rejected);
      item.head.textContent = info.title + " · " + (labelFrom(toolNames, data.name) || "Инструмент");
      paragraphs(item.body, info.lines);
      if (!item.started && !rejected) paragraphs(item.body, ["Аргументы не опубликованы; запуск API не подтверждён этой карточкой."]);
    } else paragraphs(item.body, finished ? lines || ["Ответ получен, но формат результата не распознан. Подтверждение операции смотрите в технических деталях."] :
      ["Запрос передан инструменту. Результат ещё не получен."]);
    if (failedState) paragraphs(item.body, ["Проверьте сообщения STE Slicer и состояние по ID. Не повторяйте запуск автоматически."]);
    if (finished && Array.isArray(result.argument_coercions) && result.argument_coercions.length) {
      paragraphs(item.body, ["Аргументы модели исправлены перед запуском: " + result.argument_coercions.join("; ") + "."]);
    }
    if (finished && result.ok === true) {
      if (data.name === "load_model_file" && result.data) markImported(result.data.file_id);
      if (/^(load_model_file|load_model|move_model|rotate_model|scale_model|delete_model|get_arrangement_status|set_print_mode)$/.test(data.name)) sceneSoon();
    }
    technical(item.card, {tool: data.name, arguments: item.started ? item.arguments : "Не опубликованы", result: finished ? result : "Ожидается"});
  }
  function renderSlice(slice) {
    var target = document.getElementById("slice-state");
    clear(target);
    if (!slice) { target.textContent = "Снимков нарезки ещё нет."; return; }
    paragraphs(target, ["Нарезка: " + stateText(slice.state), progressText(slice.progress),
      slice.state === "completed" ? slice.valid ? "Актуально на момент чтения." : "УСТАРЕЛ / актуальность не подтверждена." : null,
      slice.error ? slice.error.message : null]);
    if (slice.result) paragraphs(target, slice.result.source === "ste_slicer" ? estimates(slice.result) : ["Источник результата не подтверждён."]);
    observationWarning(target, slice.observation);
    technical(target, {snapshot: slice, observation: observationText(slice.observation)});
  }
  function observationWarning(target, observation) {
    if (observation && observation.state === "stopped") target.appendChild(element("p",
      "Обновление не удалось: операция могла продолжиться. Проверьте состояние в STE Slicer.", "observation-warning"));
  }
  function observationText(observation) {
    if (!observation) return "Снимок из tools; автонаблюдение этого запуска не подтверждено.";
    if (observation.state === "watching") return "Свой запуск обновляется автоматически без запросов LLM; завершение ответа агента не завершает операцию.";
    if (observation.state === "stopped") return "Наблюдение прервано: " + (observation.error && observation.error.message || "проверьте UI") + "; слайсер мог продолжить работу.";
    return "Наблюдение завершено; дальнейшие ручные изменения проверяются при чтении/скачивании.";
  }
  function renderExport(value) {
    var target = document.getElementById("export-state");
    clear(target);
    if (!value) { target.textContent = "Своих экспортов ещё нет."; return; }
    paragraphs(target, ["Экспорт " + (value.kind === "project" ? "проекта 3MF" : value.kind === "gcode" ? "G-code" : "файла") + ": " + stateText(value.state),
      progressText(value.progress), value.state === "completed" && !value.valid ? "УСТАРЕЛ / актуальность не подтверждена." : null,
      value.error ? value.error.message : null]);
    observationWarning(target, value.observation);
    technical(target, {snapshot: value, observation: observationText(value.observation)});
  }
  function renderArtifact(artifact) {
    if (!artifact || typeof artifact.artifact_id !== "string") return;
    var row = artifacts[artifact.artifact_id];
    if (!row) {
      row = document.createElement("div");
      artifacts[artifact.artifact_id] = row; elArtifacts.appendChild(row);
    }
    while (row.firstChild) row.removeChild(row.firstChild);
    row.textContent = artifact.file_name + " · " + (artifact.kind === "project" ? "Проект 3MF" : artifact.kind === "gcode" ? "G-code" : "Файл") +
      " · " + (number(artifact.size_bytes) ? valueText(artifact.size_bytes) + " байт" : "размер не сообщён") + " · Нарезка: " + artifact.slice_id;
    if (artifact.valid === true && typeof artifact.download_url === "string" &&
        /^\/api\/files\/[A-Za-z0-9_-]{1,128}$/.test(artifact.download_url)) {
      var link = document.createElement("a");
      link.textContent = " Скачать"; link.href = artifact.download_url;
      link.target = "_blank"; link.rel = "noopener";
      row.appendChild(link);
    } else row.textContent += artifact.valid === false ? " — устарел / недоступен" : " — локальный результат, скачивание не подтверждено";
    if (artifact.download_error) row.textContent += " — " + artifact.download_error.message;
    technical(row, artifact);
  }
  function applyEvent(event) {
    var data = event.data || {};
    if (event.type === "agent_state") {
      sessionBusy = data.state === "processing";
      if (!sessionBusy) globalBusy = false;
      elState.textContent = sessionBusy ? "Агент выполняет запрос..." : "Ответ агента завершён. Состояние нарезки и экспорта показано отдельно.";
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
    processId = snapshot.process_id || processId;
    if (pending && ((pending.process_id && processId && pending.process_id !== processId) || (!pending.process_id && snapshot.restored))) {
      expiredPending = true; pending = null; dropSelected(); save("ste_ai_pending", null); elInput.value = "";
    }
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
    if (expiredPending) {
      errorMessage("Неподтверждённая отправка прежнего процесса истекла и автоматически не повторялась. Проверьте историю и текущее состояние STE.");
      expiredPending = false;
    }
    after = snapshot.last_event_id;
    sessionBusy = snapshot.agent_state === "processing";
    operationBusy = !!snapshot.operation_busy;
    document.getElementById("chat-title").textContent = snapshot.title || "Диалог с ИИ-технологом";
    renderPersistence(snapshot.persistence, snapshot.history_truncated);
    renderSlice(snapshot.slice);
    renderExport(snapshot.export);
    if (pending && pending.session_id === snapshot.session_id &&
        snapshot.messages.some(function (m) { return m.client_message_id === pending.client_message_id; })) {
      pending = null; dropSelected(); save("ste_ai_pending", null); elInput.value = "";
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
      sessionId = snapshot.session_id; pending = null; dropSelected(); after = 0;
      save("ste_ai_session_id", sessionId); save("ste_ai_pending", null); adopt(snapshot);
      elInput.value = ""; historyChoice = sessionId;
      return loadSessions();
    });
  }
  function loadStatus() {
    return api("/api/status").then(function (status) {
      var restarted = processId && status.process_id && processId !== status.process_id;
      globalBusy = status.agent_state === "processing";
      operationBusy = !!status.operation_busy;
      processId = status.process_id || processId;
      readLimits(status);
      if (pending && pending.process_id && processId && pending.process_id !== processId) {
        expiredPending = true; pending = null; dropSelected(); save("ste_ai_pending", null);
      }
      document.getElementById("status-badge").textContent = status.llm.configured ?
        "LLM настроена; связь не проверена" : "Нужны настройки LLM";
      document.getElementById("cfg-state").textContent = status.llm.has_key ? "Ключ сохранён в Preferences." : "Ключ не подтверждён.";
      document.getElementById("st-printer").textContent = status.slicer.machine_name || "STE недоступен / машина не выбрана";
      document.getElementById("st-mode").textContent = status.slicer.printing_mode ? modeText(status.slicer.printing_mode) : "—";
      buttons();
      if (restarted && sessionId) {
        ++generation; dropSelected();
        return restore(); // New process: replace old cards/capabilities using a read only.
      }
    });
  }
  function readLimits(status) {
    var value = status.limits || {};
    if (number(value.files_per_message)) limits.files = value.files_per_message;
    if (number(value.image_bytes)) limits.image = value.image_bytes;
    if (number(value.file_bytes)) limits.model = value.file_bytes;
    if (number(value.images_per_message_bytes)) limits.imagesTotal = value.images_per_message_bytes;
    if (Array.isArray(status.accepted_extensions) && status.accepted_extensions.length) limits.extensions = status.accepted_extensions.slice();
    document.getElementById("attachment-limits").textContent = "Вложения: фото JPG/PNG до " + sizeText(limits.image) +
      ", модели STL/OBJ/3MF до " + sizeText(limits.model) + "; до " + limits.files + " файлов";
  }

  // Scene panel: live objects through the same MCP registry (no new REST routes, no JS file access).
  var elSceneList = document.getElementById("scene-list"), elSceneStatus = document.getElementById("scene-status");
  var elSceneResult = document.getElementById("scene-result"), elSceneReason = document.getElementById("scene-reason");
  var sceneControls = ["btn-rotate", "btn-move", "btn-scale", "btn-delete-model"].map(function (id) { return document.getElementById(id); });
  var sceneTimer = null;
  function rpc(name, args) {
    return post("/mcp", {jsonrpc: "2.0", id: ++rpcId, method: "tools/call", params: {name: name, arguments: args || {}}}).then(function (body) {
      if (body.error) {
        var error = new Error(body.error.message || "Аргументы отклонены");
        error.rejected = [-32600, -32601, -32602].indexOf(body.error.code) !== -1;
        throw error;
      }
      var content = body.result && body.result.content && body.result.content[0];
      return JSON.parse(content && content.text || "null") || {ok: false, error: {message: "Пустой ответ инструмента"}};
    });
  }
  function sceneReason() {
    if (globalBusy || sessionBusy) return "Агент выполняет запрос — ручные изменения сцены временно недоступны.";
    if (operationBusy) return "Идёт нарезка или запись файла — дождитесь завершения.";
    if (uploading) return "Идёт загрузка вложения.";
    if (switching) return "Переключение диалога.";
    if (sceneBusy) return "Выполняется предыдущее действие со сценой.";
    if (!sceneChoice) return sceneModels.length ? "Выберите модель в списке." : sceneRead ? "Сцена пуста — действовать не с чем." : "Сцена ещё не прочитана.";
    return "";
  }
  function sceneButtons() {
    if (!sceneControls) return; // buttons() may run before the scene panel is initialised.
    var reason = sceneReason();
    sceneControls.forEach(function (button) { button.disabled = !!reason; });
    document.getElementById("btn-scene-refresh").disabled = sceneBusy;
    elSceneReason.textContent = reason;
  }
  function sceneSoon() {
    if (sceneTimer) return;
    sceneTimer = true;
    Promise.resolve().then(function () { sceneTimer = null; return refreshScene(); });
  }
  function fillPosition() {
    var position = sceneState && sceneState.position_mm;
    ["x", "y", "z"].forEach(function (axis) {
      document.getElementById("move-" + axis).value = position && number(position[axis]) ? String(Math.round(position[axis] * 1000) / 1000) : "";
    });
  }
  function renderScene() {
    clear(elSceneList);
    sceneModels.forEach(function (model) {
      var size = model.bounds && model.bounds.size;
      var row = element("button", (model.name || "Без имени") + (size ? " · " + ["x", "y", "z"].map(function (axis) {
        return valueText(Math.round(size[axis] * 10) / 10); }).join(" × ") + " мм" : ""), "history-entry" + (model.object_id === sceneChoice ? " current" : ""));
      row.type = "button";
      row.title = "ID " + model.object_id + (model.is_group ? " (группа)" : "");
      row.setAttribute && row.setAttribute("aria-pressed", model.object_id === sceneChoice ? "true" : "false");
      row.addEventListener("click", function () {
        if (sceneChoice === model.object_id) return;
        sceneChoice = model.object_id; sceneState = null; clear(elSceneResult);
        renderScene(); refreshScene();
      });
      elSceneList.appendChild(row);
    });
    document.getElementById("scene-controls").hidden = !sceneModels.length; // Nothing to act on.
    var chosen = sceneModels.filter(function (model) { return model.object_id === sceneChoice; })[0];
    var selectedText = document.getElementById("scene-selected");
    if (!chosen) selectedText.textContent = sceneModels.length ? "Выберите модель в списке." : "Нет модели для действий.";
    else {
      selectedText.textContent = "Выбрана: " + (chosen.name || chosen.object_id) + (sceneState ? ". Позиция X / Y↑ / Z: " + positionText(sceneState.position_mm) +
        (sceneState.outside_build_area === true ? ". Вне области печати!" : "") : "");
    }
    sceneButtons();
  }
  function refreshScene() {
    if (sceneBusy) return Promise.resolve();
    sceneBusy = true; sceneButtons();
    var previous = sceneChoice;
    return rpc("list_models").then(function (result) {
      if (!result.ok) {
        sceneModels = []; sceneChoice = null; sceneState = null;
        elSceneStatus.textContent = "Сцена недоступна: " + (result.error && result.error.message || "ошибка чтения");
        return;
      }
      sceneRead = true;
      sceneModels = Array.isArray(result.data.models) ? result.data.models : [];
      elSceneStatus.textContent = sceneModels.length ? "Объектов в сцене: " + sceneModels.length + ". Данные прочитаны из STE сейчас." :
        "Сцена пуста. Прикрепите STL/OBJ/3MF и попросите агента импортировать модель.";
      if (sceneModels.length === 1 && !sceneChoice) sceneChoice = sceneModels[0].object_id;
      if (sceneChoice && !sceneModels.some(function (model) { return model.object_id === sceneChoice; })) {
        sceneChoice = null; sceneState = null;
        elSceneStatus.textContent += " Выбранный ранее объект больше не существует (удалён, перезапуск или импорт проекта).";
      }
      if (!sceneChoice) return;
      return rpc("get_model_state", {object_id: sceneChoice}).then(function (state) {
        sceneState = state.ok ? state.data : null;
        if (sceneChoice !== previous || !document.getElementById("move-x").value) fillPosition();
      });
    }).catch(function (err) {
      elSceneStatus.textContent = "Не удалось прочитать сцену: " + err.message;
    }).then(function () { sceneBusy = false; renderScene(); });
  }
  function parseNumber(id, label, example) {
    var raw = String(document.getElementById(id).value || "").trim().replace(",", ".");
    if (!raw) return {error: label + ": поле пустое. Введите число, например " + example + "."};
    if (!/^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(raw)) return {error: label + ": ожидается число без единиц, например " + example + "."};
    var value = Number(raw);
    if (!isFinite(value)) return {error: label + ": число вне допустимого диапазона."};
    return {value: value};
  }
  function sceneOutcome(title, lines, kind) {
    clear(elSceneResult);
    elSceneResult.className = "scene-result " + (kind || "");
    elSceneResult.appendChild(element("strong", title));
    paragraphs(elSceneResult, lines);
  }
  function sceneAction(name, args, label) {
    var reason = sceneReason();
    if (reason) { sceneOutcome("Действие недоступно", [reason], "fail"); return; }
    sceneBusy = true; sceneButtons();
    sceneOutcome(label + ": выполняется…", ["Ждём подтверждения от STE Slicer."], "neutral");
    rpc(name, args).then(function (result) {
      if (result.ok) {
        var lines = name === "delete_model" ? ["Модель удалена из сцены; исходный файл не удалён. Отмена: Ctrl+Z в STE Slicer."] : transformLines(result.data);
        sceneOutcome(label + ": готово", lines.concat(["Прежняя нарезка и скачивания помечены устаревшими — нарежьте заново."]), "ok");
      } else {
        var info = problem(result.error);
        sceneOutcome(info.title, ["Действие не выполнено или не подтверждено."].concat(info.lines), "fail");
        technical(elSceneResult, result.error);
      }
    }, function (err) {
      sceneOutcome(err.rejected ? "Отклонено до запуска" : "Результат действия не подтверждён", [err.message, err.rejected ? "Сцена этим действием не менялась." : "Связь с плагином прервалась; результат не подтверждён — нажмите «Обновить». Не повторяйте действие до проверки сцены."], "fail");
    }).then(function () { sceneBusy = false; return refreshScene(); });
  }
  document.getElementById("btn-scene-refresh").addEventListener("click", function () { refreshScene(); });
  document.getElementById("btn-rotate").addEventListener("click", function () {
    var angle = parseNumber("rot-angle", "Угол", "90");
    if (angle.error) { sceneOutcome("Проверьте ввод", [angle.error, "Сцена не менялась."], "fail"); return; }
    sceneAction("rotate_model", {object_id: sceneChoice, axis: document.getElementById("rot-axis").value || "x", angle_degrees: angle.value}, "Поворот");
  });
  document.getElementById("btn-move").addEventListener("click", function () {
    var position = {}, errors = [];
    ["x", "y", "z"].forEach(function (axis) {
      var parsed = parseNumber("move-" + axis, axis.toUpperCase(), "0");
      if (parsed.error) errors.push(parsed.error); else position[axis] = parsed.value;
    });
    if (errors.length) { sceneOutcome("Проверьте ввод", errors.concat(["Сцена не менялась."]), "fail"); return; }
    sceneAction("move_model", {object_id: sceneChoice, position_mm: position}, "Перемещение");
  });
  document.getElementById("btn-scale").addEventListener("click", function () {
    var factors = {}, errors = [];
    ["x", "y", "z"].forEach(function (axis) {
      var parsed = parseNumber("scale-" + axis, "Масштаб " + axis.toUpperCase(), "1.5");
      if (parsed.error) errors.push(parsed.error);
      else if (parsed.value < 0.01 || parsed.value > 100) errors.push("Масштаб " + axis.toUpperCase() + ": допустимо от 0,01 до 100.");
      else factors[axis] = parsed.value;
    });
    if (errors.length) { sceneOutcome("Проверьте ввод", errors.concat(["Сцена не менялась."]), "fail"); return; }
    sceneAction("scale_model", {object_id: sceneChoice, factors: factors}, "Масштаб");
  });
  document.getElementById("btn-delete-model").addEventListener("click", function () {
    var chosen = sceneModels.filter(function (model) { return model.object_id === sceneChoice; })[0];
    if (!chosen || typeof confirm !== "function" || !confirm("Удалить «" + (chosen.name || chosen.object_id) +
        "» из сцены STE? Исходный файл не удаляется; отменить можно Ctrl+Z в STE Slicer.")) return;
    sceneAction("delete_model", {object_id: sceneChoice}, "Удаление");
  });

  function pollPage(current) {
    return api("/api/events?session_id=" + encodeURIComponent(sessionId) + "&after=" + after).then(function (page) {
      if (current !== generation) return;
      if (page.process_id && processId && page.process_id !== processId) {
        processId = page.process_id;
        if (pending) { expiredPending = true; pending = null; save("ste_ai_pending", null); }
        dropSelected();
        return restore();
      }
      page.events.forEach(function (event) {
        if (event.id > after) { applyEvent(event); after = event.id; }
      });
      renderPersistence(page.persistence);
      after = page.next_after;
      if (page.has_more) return pollPage(current);
    });
  }
  function poll() {
    if (!sessionId || polling || switching) return;
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
    if (!text || !sessionId || globalBusy || sessionBusy || operationBusy || switching || uploading || selected.some(function (item) { return !!item.error; })) return;
    if (pending && (pending.text !== text || pending.session_id !== sessionId)) {
      errorMessage("Сначала проверьте/повторите прежнюю неподтверждённую попытку; новый ID автоматически не создаётся.");
      return;
    }
    if (!pending) pending = {session_id: sessionId, process_id: processId, text: text,
      attachment_ids: selected.map(function (item) { return item.file_id; }),
      attachments: selected.map(function (item) { return {file_id: item.file_id, name: item.name}; }),
      client_message_id: "client_" + Date.now() + "_" + Math.random().toString(36).slice(2)};
    save("ste_ai_pending", JSON.stringify(pending));
    sessionBusy = true; attachmentList(); buttons();
    var attempt = pending;
    post("/api/sessions/" + encodeURIComponent(sessionId) + "/messages",
      {text: attempt.text, attachment_ids: attempt.attachment_ids || [], client_message_id: attempt.client_message_id}).then(function () {
        if (pending !== attempt || attempt.session_id !== sessionId || (attempt.process_id && processId && attempt.process_id !== processId)) return;
        // Read the accepted user message; never add a second optimistic copy.
        pending = null; dropSelected(); save("ste_ai_pending", null); elInput.value = "";
        return restore();
      }).catch(function (err) {
        if (pending !== attempt || attempt.session_id !== sessionId || (attempt.process_id && processId && attempt.process_id !== processId)) return;
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
  elInput.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!elSend.disabled) send(elInput.value); }
  });
  elNew.addEventListener("click", function () { if (!elNew.disabled) newSession().catch(function (err) { errorMessage(err.message); }); });
  elOpen.addEventListener("click", function () { if (!elOpen.disabled) openSession(historyChoice).catch(function (err) { elHistoryStatus.textContent = err.message; }); });
  elDelete.addEventListener("click", deleteSession);
  var chips = document.querySelectorAll(".chip");
  for (var i = 0; i < chips.length; i++) {
    chips[i].addEventListener("click", function () { elInput.value = this.getAttribute("data-prompt"); send(elInput.value); });
  }
  function bootstrap() {
    if (!archiveReady) return Promise.resolve();
    bootAttempted = true;
    return sessionId ? restore().catch(function (err) {
      if (err.status === 404) return newSession().then(function () {
        errorMessage("История процесса сброшена. Проверьте проект; прежние изменения автоматически не повторялись.");
      });
      throw err;
    }) : historyEntries.length ? openSession(historyEntries[0].session_id) : newSession();
  }
  loadStatus().then(loadSessions).then(bootstrap).catch(function (err) { errorMessage(err.message); }).then(function () { return refreshScene(); });
  var ticks = 0;
  setInterval(function () {
    ticks++;
    if (failures && ticks % Math.min(10, failures + 1)) return;
    poll();
    if (ticks % 15 === 0 && !sceneBusy && !sessionBusy && !globalBusy) refreshScene();
    if (ticks % 10 === 0 || !archiveReady) loadStatus().then(loadSessions).then(function () {
      if (!bootAttempted && archiveReady) return bootstrap();
    }).catch(function (err) { elHistoryStatus.textContent = "Список недоступен: " + err.message; });
  }, 1000);
})();
