(function () {
  var sessionId = localStorage.getItem("ste_ai_session_id") || null;
  var lastSeq = 0;
  var toolElements = {};

  var elMessages = document.getElementById("messages");
  var elForm = document.getElementById("chat-form");
  var elInput = document.getElementById("chat-input");
  var elSend = document.getElementById("btn-send");
  var elStateText = document.getElementById("agent-state-text");
  var elBadge = document.getElementById("status-badge");

  function updateSlicerPanel(slicer) {
    if (!slicer) return;
    document.getElementById("st-printer").textContent = slicer.printer || "—";
    document.getElementById("st-mode").textContent = slicer.printing_mode || "—";
    document.getElementById("st-quality").textContent = slicer.active_quality_type || "—";
    document.getElementById("st-material").textContent = slicer.active_material_id || "—";
    document.getElementById("st-layer").textContent =
      slicer.layer_height !== undefined && slicer.layer_height !== null
        ? slicer.layer_height + " мм"
        : "—";
  }

  function appendMessage(role, text, isError) {
    var div = document.createElement("div");
    div.className = "msg " + (isError ? "error" : role);
    div.textContent = text;
    elMessages.appendChild(div);
    elMessages.scrollTop = elMessages.scrollHeight;
  }

  function renderToolStarted(data) {
    var card = document.createElement("div");
    card.className = "tool-card";
    var head = document.createElement("div");
    head.className = "tool-head";
    head.textContent = "⚙ Вызов: " + data.name + " (" + data.tool_call_id + ")";
    var body = document.createElement("div");
    body.className = "tool-body";
    body.textContent = "Аргументы: " + JSON.stringify(data.arguments);
    card.appendChild(head);
    card.appendChild(body);
    elMessages.appendChild(card);
    elMessages.scrollTop = elMessages.scrollHeight;
    toolElements[data.tool_call_id] = { card: card, head: head, body: body };
  }

  function renderToolFinished(data) {
    var item = toolElements[data.tool_call_id];
    if (!item) {
      renderToolStarted({ tool_call_id: data.tool_call_id, name: data.name, arguments: {} });
      item = toolElements[data.tool_call_id];
    }
    item.card.className = "tool-card " + (data.ok ? "ok" : "fail");
    item.head.textContent = (data.ok ? "✓ " : "✗ ") + data.name + " (" + data.tool_call_id + ")";
    item.body.textContent += "\nРезультат: " + JSON.stringify(data.result);
    elMessages.scrollTop = elMessages.scrollHeight;
  }

  function handleEvent(ev) {
    lastSeq = Math.max(lastSeq, ev.seq);
    var t = ev.type;
    var d = ev.data || {};
    if (t === "agent_state") {
      if (d.state === "running") {
        elStateText.textContent = "Агент выполняет запрос...";
        elSend.disabled = true;
      } else if (d.state === "error") {
        elStateText.textContent = "Ошибка выполнения";
        elSend.disabled = false;
      } else {
        elStateText.textContent = "Готов к работе";
        elSend.disabled = false;
      }
    } else if (t === "tool_started") {
      renderToolStarted(d);
    } else if (t === "tool_finished") {
      renderToolFinished(d);
    } else if (t === "assistant_message") {
      appendMessage("assistant", d.text, d.is_error);
    }
  }

  function loadStatus() {
    return fetch("/api/status")
      .then(function (r) { return r.json(); })
      .then(function (st) {
        document.getElementById("cfg-url").value = st.base_url || "";
        document.getElementById("cfg-model").value = st.model || "";
        document.getElementById("cfg-key-indicator").textContent =
          st.has_api_key ? "(ключ сохранён)" : "(не задан)";
        elBadge.textContent = st.configured ? "LLM подключена" : "Нужен API-ключ";
        elBadge.className = "badge " + (st.configured ? "ok" : "warn");
        updateSlicerPanel(st.slicer);
      });
  }

  function startNewSession() {
    return fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    })
      .then(function (r) { return r.json(); })
      .then(function (sess) {
        sessionId = sess.id;
        lastSeq = 0;
        toolElements = {};
        localStorage.setItem("ste_ai_session_id", sessionId);
        elMessages.innerHTML = "";
        appendMessage(
          "assistant",
          "Сессия создана. Опишите задачу печати или попросите прочитать/изменить настройки слайсера.",
          false
        );
      });
  }

  function initSession() {
    if (!sessionId) {
      return startNewSession();
    }
    return fetch("/api/sessions/" + encodeURIComponent(sessionId))
      .then(function (r) {
        if (!r.ok) throw new Error("not found");
        return r.json();
      })
      .then(function (sess) {
        elMessages.innerHTML = "";
        lastSeq = 0;
        return pollEvents();
      })
      .catch(function () {
        return startNewSession();
      });
  }

  function pollEvents() {
    if (!sessionId) return Promise.resolve();
    return fetch("/api/sessions/" + encodeURIComponent(sessionId) + "/events?after=" + lastSeq)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.slicer) updateSlicerPanel(data.slicer);
        var evs = data.events || [];
        for (var i = 0; i < evs.length; i++) {
          handleEvent(evs[i]);
        }
      })
      .catch(function () {});
  }

  function sendMessage(text) {
    if (!text.trim() || !sessionId) return;
    var cmid = "cm_" + Date.now() + "_" + Math.random().toString(36).slice(2, 7);
    appendMessage("user", text, false);
    elInput.value = "";
    elSend.disabled = true;

    fetch("/api/sessions/" + encodeURIComponent(sessionId) + "/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, client_message_id: cmid })
    })
      .then(function (r) {
        return r.json().then(function (b) { return { status: r.status, body: b }; });
      })
      .then(function (res) {
        if (res.status !== 202) {
          appendMessage("assistant", res.body.error || "Ошибка отправки запроса", true);
          elSend.disabled = false;
        } else {
          pollEvents();
        }
      })
      .catch(function (err) {
        appendMessage("assistant", "Сбой соединения: " + err.message, true);
        elSend.disabled = false;
      });
  }

  elForm.addEventListener("submit", function (e) {
    e.preventDefault();
    sendMessage(elInput.value);
  });

  document.getElementById("btn-new-session").addEventListener("click", function () {
    startNewSession();
  });

  document.getElementById("btn-save-cfg").addEventListener("click", function () {
    var payload = {
      base_url: document.getElementById("cfg-url").value,
      model: document.getElementById("cfg-model").value,
      api_key: document.getElementById("cfg-key").value
    };
    fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function () {
      document.getElementById("cfg-key").value = "";
      loadStatus();
    });
  });

  document.getElementById("btn-clear-key").addEventListener("click", function () {
    fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clear_api_key: true })
    }).then(function () {
      loadStatus();
    });
  });

  var chips = document.querySelectorAll(".chip");
  for (var i = 0; i < chips.length; i++) {
    chips[i].addEventListener("click", function () {
      sendMessage(this.getAttribute("data-prompt"));
    });
  }

  loadStatus().then(initSession);
  setInterval(pollEvents, 1000);
})();