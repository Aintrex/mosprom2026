// Frontend SPA для STE Slicer AI Assistant (чистый JS без фреймворков)

var pendingImages = [];
var pendingModelPath = null;
var pendingModelName = null;
var activeCaseFolder = "custom_model";

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function renderMarkdown(md) {
  var html = escapeHtml(md);
  html = html.replace(/^### (.*)$/gm, "<h3>$1</h3>");
  html = html.replace(/^#### (.*)$/gm, "<h4>$1</h4>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");

  var lines = html.split("\n");
  var out = [];
  var inList = false;
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    if (/^\s*[-*]\s+/.test(line)) {
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push("<li>" + line.replace(/^\s*[-*]\s+/, "") + "</li>");
    } else {
      if (inList) {
        out.push("</ul>");
        inList = false;
      }
      if (line.trim() === "") {
        out.push("");
      } else if (line.indexOf("<h3>") === 0 || line.indexOf("<h4>") === 0) {
        out.push(line);
      } else {
        out.push("<p>" + line + "</p>");
      }
    }
  }
  if (inList) out.push("</ul>");
  return out.join("\n");
}

function updateAttachmentsBar() {
  var bar = document.getElementById("attachmentsBar");
  bar.innerHTML = "";
  if (!pendingModelName && pendingImages.length === 0) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");

  if (pendingModelName) {
    var chip = document.createElement("div");
    chip.className = "att-chip";
    chip.innerHTML =
      "<span>📦 <strong>" +
      escapeHtml(pendingModelName) +
      "</strong></span><button type='button' title='Удалить'>✕</button>";
    chip.querySelector("button").onclick = function () {
      pendingModelPath = null;
      pendingModelName = null;
      updateAttachmentsBar();
    };
    bar.appendChild(chip);
  }

  pendingImages.forEach(function (imgObj, idx) {
    var chip = document.createElement("div");
    chip.className = "att-chip";
    chip.innerHTML =
      "<span>🖼 " +
      escapeHtml(imgObj.name) +
      "</span><button type='button' title='Удалить'>✕</button>";
    chip.querySelector("button").onclick = function () {
      pendingImages.splice(idx, 1);
      updateAttachmentsBar();
    };
    bar.appendChild(chip);
  });
}

function updateDashboard(state) {
  if (!state) return;

  document.getElementById("runtimeBadge").textContent = state.runtime_mode || "STE Slicer";
  document.getElementById("printerName").textContent = state.printer || "Stereotech Hybrid 530 V5.2.4";

  var modeBadge = document.getElementById("printingModeBadge");
  var mode = state.printing_mode || "classic";
  modeBadge.textContent = state.printing_mode_label || mode;
  modeBadge.className = "mode-badge mode-" + mode;

  var matName = state.active_material;
  if (state.available_materials) {
    state.available_materials.forEach(function (m) {
      if (m.id === state.active_material) matName = m.name;
    });
  }
  document.getElementById("activeMaterial").textContent = matName || "—";
  document.getElementById("activeQuality").textContent = state.active_quality || "normal";

  var s = state.settings || {};
  document.getElementById("setLayerHeight").textContent = (s.layer_height || 0.2) + " мм";
  document.getElementById("setWalls").textContent =
    (s.wall_line_count || 3) + " (" + (s.wall_thickness || 1.2) + " мм)";
  document.getElementById("setInfill").textContent =
    (s.infill_sparse_density !== undefined ? s.infill_sparse_density : 20) +
    "% (" +
    (s.infill_pattern || "grid") +
    ")";
  document.getElementById("setSupports").textContent = s.support_enable ? "Вкл" : "Выкл";
  document.getElementById("setTemps").textContent =
    (s.material_print_temp || 210) + "° / " + (s.material_bed_temp || 60) + "°C";
  document.getElementById("setSpeed").textContent =
    (s.speed_print || 50) + " мм/с · Обдув " + (s.cool_fan_enabled ? "Вкл" : "Выкл");

  var sceneBox = document.getElementById("sceneModelsContainer");
  var models = state.scene_models || [];
  if (models.length === 0) {
    sceneBox.className = "scene-empty";
    sceneBox.innerHTML = "Модель ещё не загружена в сцену";
  } else {
    sceneBox.className = "";
    sceneBox.innerHTML = models
      .map(function (m) {
        var d = m.dimensions_mm || { x: 0, y: 0, z: 0 };
        return (
          "<div class='scene-model-item'>" +
          "<strong>📦 " +
          escapeHtml(m.name) +
          "</strong>" +
          "<div>Габариты: <b>" +
          d.x +
          " × " +
          d.y +
          " × " +
          d.z +
          " мм</b></div>" +
          "<div>Объём: <b>" +
          m.volume_cm3 +
          " см³</b> · Граней: " +
          m.triangles_count +
          "</div>" +
          "<div>Нависания &gt;45°: <b>" +
          m.overhang_percent +
          "%</b> · Симметрия XY: <b>" +
          m.cylindrical_symmetry_score +
          "</b></div>" +
          "</div>"
        );
      })
      .join("");
  }

  var sl = state.slicing_state || {};
  document.getElementById("sliceProgressBar").style.width = (sl.progress || 0) + "%";
  var stMap = {
    idle: "Ожидание",
    slicing: "Нарезка...",
    completed: "Готово (100%)",
    error: "Ошибка"
  };
  document.getElementById("sliceStatusText").textContent = stMap[sl.status] || sl.status || "Ожидание";
  document.getElementById("sliceTimeText").textContent = sl.print_time_formatted || "—";
  document.getElementById("sliceMaterialText").textContent = sl.filament_weight_g
    ? sl.filament_weight_g + " г (" + sl.filament_length_m + " м)"
    : "—";

  if (state.config && state.config.model) {
    document.getElementById("llmModelBadge").textContent =
      state.config.model + (state.config.has_api_key ? " · ключ активен" : " · без ключа (авторежим)");
  }

  if (state.preset_cases && document.getElementById("presetCasesList").children.length === 0) {
    var listEl = document.getElementById("presetCasesList");
    state.preset_cases.forEach(function (c) {
      var btn = document.createElement("button");
      btn.className = "preset-btn";
      var badgeLabel =
        c.recommended_mode === "classic"
          ? "3D"
          : c.recommended_mode === "cylindrical"
          ? "5D Spiral"
          : "5D Full";
      btn.innerHTML =
        "<span>" +
        escapeHtml(c.title) +
        "</span><span class='preset-tag'>" +
        badgeLabel +
        "</span>";
      btn.onclick = function () {
        loadPresetCase(c.id);
      };
      listEl.appendChild(btn);
    });
  }
}

function appendUserMessage(text, images, modelName) {
  var container = document.getElementById("chatMessages");
  var div = document.createElement("div");
  div.className = "message user-message";

  var metaInfo = modelName ? "📦 " + escapeHtml(modelName) : "Пользователь";
  var imgsHtml = "";
  if (images && images.length > 0) {
    imgsHtml =
      "<div class='msg-images'>" +
      images
        .map(function (src) {
          return "<img src='" + src + "' alt='attachment' />";
        })
        .join("") +
      "</div>";
  }

  div.innerHTML =
    "<div class='msg-header'><span class='msg-role'>👤 Вы</span><span>" +
    metaInfo +
    "</span></div>" +
    "<div class='msg-body'>" +
    renderMarkdown(text) +
    imgsHtml +
    "</div>";

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function appendAssistantMessage(replyText, toolCalls, warningText) {
  var container = document.getElementById("chatMessages");
  var div = document.createElement("div");
  div.className = "message assistant-message";

  var toolsHtml = "";
  if (toolCalls && toolCalls.length > 0) {
    toolsHtml =
      "<div class='tool-calls-container'>" +
      toolCalls
        .map(function (tc) {
          return (
            "<details class='tool-card'>" +
            "<summary><span>⚡ MCP tools/call → " +
            escapeHtml(tc.tool) +
            "</span><span>" +
            (tc.duration_ms || 1) +
            " мс · ОК</span></summary>" +
            "<pre>Аргументы: " +
            escapeHtml(JSON.stringify(tc.arguments, null, 2)) +
            "\n\nРезультат: " +
            escapeHtml(JSON.stringify(tc.result, null, 2)) +
            "</pre>" +
            "</details>"
          );
        })
        .join("") +
      "</div>";
  }

  var warnHtml = "";
  if (warningText) {
    warnHtml =
      "<p style='font-size:12px;color:#fbbf24;margin-bottom:8px;'>ℹ️ " +
      escapeHtml(warningText) +
      "</p>";
  }

  div.innerHTML =
    "<div class='msg-header'>" +
    "<span class='msg-role'>🤖 STE Slicer AI Assistant</span>" +
    "<span class='msg-meta'>Выполнено MCP-вызовов: " +
    (toolCalls ? toolCalls.length : 0) +
    "</span>" +
    "</div>" +
    toolsHtml +
    warnHtml +
    "<div class='msg-body'>" +
    renderMarkdown(replyText) +
    "</div>";

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function sendMessage() {
  var input = document.getElementById("userInput");
  var text = input.value.trim();
  if (!text && !pendingModelPath && pendingImages.length === 0) return;

  var imgs = pendingImages.map(function (item) {
    return item.dataUrl;
  });
  var modPath = pendingModelPath;
  var modName = pendingModelName;

  appendUserMessage(text || "Настрой печать прикреплённой 3D-модели.", imgs, modName);

  input.value = "";
  pendingImages = [];
  pendingModelPath = null;
  pendingModelName = null;
  updateAttachmentsBar();

  var sendBtn = document.getElementById("sendBtn");
  sendBtn.disabled = true;
  sendBtn.textContent = "Агент работает...";

  fetch("/api/message", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: text,
      images: imgs,
      model_path: modPath
    })
  })
    .then(function (r) {
      return r.json();
    })
    .then(function (data) {
      sendBtn.disabled = false;
      sendBtn.textContent = "Отправить ▶";
      appendAssistantMessage(data.reply || "Готово.", data.tool_calls || [], data.llm_warning);
      if (data.state) updateDashboard(data.state);
    })
    .catch(function (err) {
      sendBtn.disabled = false;
      sendBtn.textContent = "Отправить ▶";
      appendAssistantMessage("Ошибка связи с сервером плагина: " + err.message, []);
    });
}

function loadPresetCase(caseId) {
  fetch("/api/load_preset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ case_id: caseId })
  })
    .then(function (r) {
      return r.json();
    })
    .then(function (data) {
      if (data.error) {
        alert(data.error);
        return;
      }
      activeCaseFolder = data.case.folder;
      pendingModelPath = data.stl_path;
      pendingModelName = data.case.stl;
      pendingImages = [];
      if (data.image_data_url) {
        pendingImages.push({ name: data.case.jpg, dataUrl: data.image_data_url });
      }
      document.getElementById("userInput").value = data.prompt_text;
      updateAttachmentsBar();
      if (data.state) updateDashboard(data.state);
      sendMessage();
    });
}

document.getElementById("modelFileInput").addEventListener("change", function (e) {
  var file = e.target.files[0];
  if (!file) return;
  var reader = new FileReader();
  reader.onload = function (ev) {
    fetch("/api/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_name: file.name,
        content_base64: ev.target.result
      })
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (res) {
        pendingModelPath = res.file_path;
        pendingModelName = res.file_name;
        activeCaseFolder = file.name.replace(/\.[^.]+$/, "");
        updateAttachmentsBar();
        if (res.state) updateDashboard(res.state);
      });
  };
  reader.readAsDataURL(file);
});

document.getElementById("imageFileInput").addEventListener("change", function (e) {
  var files = Array.prototype.slice.call(e.target.files || []);
  files.forEach(function (file) {
    var reader = new FileReader();
    reader.onload = function (ev) {
      pendingImages.push({ name: file.name, dataUrl: ev.target.result });
      updateAttachmentsBar();
    };
    reader.readAsDataURL(file);
  });
});

document.getElementById("sendBtn").addEventListener("click", sendMessage);
document.getElementById("userInput").addEventListener("keydown", function (e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

document.getElementById("saveToResultsBtn").addEventListener("click", function () {
  fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_name: activeCaseFolder })
  })
    .then(function (r) {
      return r.json();
    })
    .then(function (res) {
      alert(
        "Проект сохранён для сдачи!\n\nПапка: " +
          res.output_dir +
          "\n• project.3mf (" +
          res.project_3mf.size_bytes +
          " байт)\n• result.gcode (" +
          res.result_gcode.size_bytes +
          " байт)"
      );
    });
});

document.getElementById("openSettingsBtn").addEventListener("click", function () {
  fetch("/api/config")
    .then(function (r) {
      return r.json();
    })
    .then(function (cfg) {
      document.getElementById("cfgBaseUrl").value = cfg.base_url || "https://api.timeweb.ai/v1";
      document.getElementById("cfgModel").value = cfg.model || "dashscope/qwen3.8-flash";
      document.getElementById("cfgExamplesDir").value = cfg.examples_dir || "";
      document.getElementById("settingsModal").classList.remove("hidden");
    });
});

function closeSettings() {
  document.getElementById("settingsModal").classList.add("hidden");
}
document.getElementById("closeSettingsBtn").addEventListener("click", closeSettings);
document.getElementById("cancelSettingsBtn").addEventListener("click", closeSettings);

document.getElementById("saveSettingsBtn").addEventListener("click", function () {
  var payload = {
    base_url: document.getElementById("cfgBaseUrl").value.trim(),
    model: document.getElementById("cfgModel").value.trim(),
    examples_dir: document.getElementById("cfgExamplesDir").value.trim()
  };
  var keyVal = document.getElementById("cfgApiKey").value.trim();
  if (keyVal) payload.api_key = keyVal;

  fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })
    .then(function (r) {
      return r.json();
    })
    .then(function () {
      closeSettings();
      fetchState();
    });
});

document.getElementById("openMcpInspectorBtn").addEventListener("click", function () {
  fetch("/mcp", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list", params: {} })
  })
    .then(function (r) {
      return r.json();
    })
    .then(function (rpc) {
      var tools = (rpc.result && rpc.result.tools) || [];
      var box = document.getElementById("mcpToolsList");
      box.innerHTML = tools
        .map(function (t) {
          return (
            "<div class='mcp-tool-item'>" +
            "<div><code>" +
            escapeHtml(t.name) +
            "</code></div>" +
            "<div style='margin-top:4px;color:#93a3b8;'>" +
            escapeHtml(t.description) +
            "</div>" +
            "</div>"
          );
        })
        .join("");
      document.getElementById("mcpModal").classList.remove("hidden");
    });
});

document.getElementById("closeMcpBtn").addEventListener("click", function () {
  document.getElementById("mcpModal").classList.add("hidden");
});

document.getElementById("clearChatBtn").addEventListener("click", function () {
  fetch("/api/reset", { method: "POST", body: "{}" })
    .then(function (r) {
      return r.json();
    })
    .then(function (res) {
      document.getElementById("chatMessages").innerHTML = "";
      appendAssistantMessage("История диалога очищена. Готов к настройке новой детали!", []);
      if (res.state) updateDashboard(res.state);
    });
});

function fetchState() {
  fetch("/api/state")
    .then(function (r) {
      return r.json();
    })
    .then(function (st) {
      updateDashboard(st);
    });
}

fetchState();
