# -*- coding: utf-8 -*-
import os
import threading
import webbrowser

try:
    from UM.Extension import Extension
    from steslicer.SteSlicerApplication import SteSlicerApplication
    from PyQt5.QtWidgets import (
        QDialog, QVBoxLayout, QFormLayout, QLineEdit,
        QPushButton, QHBoxLayout, QLabel, QCheckBox
    )
    HAS_QT = True
except Exception:
    HAS_QT = False

    class Extension(object):
        def setMenuName(self, name):
            pass

        def addMenuItem(self, name, callback):
            pass

from .slicer.adapter import SlicerAdapter, QtBridge
from .agent.tools import ToolRegistry
from .agent.mcp import MCPHandler
from .agent.service import AgentService
from .server import ThreadedHTTPServer, create_handler

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


class PreferencesConfigStore(object):
    PREF_PREFIX = "ai_assistant/"

    def __init__(self, qt_bridge):
        self._qt = qt_bridge
        self._lock = threading.Lock()
        self._cache = {
            "base_url": os.environ.get("STE_LLM_BASE_URL", "https://api.openai.com/v1"),
            "model": os.environ.get("STE_LLM_MODEL", "gpt-4o-mini"),
            "api_key": os.environ.get("STE_LLM_API_KEY", "")
        }
        self._init_from_preferences()

    def _init_from_preferences(self):
        if not HAS_QT:
            return

        def _load():
            app = SteSlicerApplication.getInstance()
            if app is None:
                return
            prefs = app.getPreferences()
            for k in ("base_url", "model", "api_key"):
                full_key = self.PREF_PREFIX + k
                prefs.addPreference(full_key, self._cache.get(k, ""))
                val = prefs.getValue(full_key)
                if val is not None and str(val).strip():
                    self._cache[k] = str(val).strip()

        try:
            self._qt.call_on_qt_thread(_load)
        except Exception:
            pass

    def get_snapshot(self):
        with self._lock:
            return dict(self._cache)

    def update_from_dict(self, data):
        with self._lock:
            if "base_url" in data and data["base_url"] is not None:
                self._cache["base_url"] = str(data["base_url"]).strip()
            if "model" in data and data["model"] is not None:
                self._cache["model"] = str(data["model"]).strip()
            if data.get("clear_api_key"):
                self._cache["api_key"] = ""
            elif "api_key" in data and data["api_key"] is not None and str(data["api_key"]).strip() != "":
                self._cache["api_key"] = str(data["api_key"]).strip()
            snapshot = dict(self._cache)

        if HAS_QT:
            def _save():
                app = SteSlicerApplication.getInstance()
                if app is None:
                    return
                prefs = app.getPreferences()
                for k in ("base_url", "model", "api_key"):
                    prefs.setValue(self.PREF_PREFIX + k, snapshot.get(k, ""))

            try:
                self._qt.call_on_qt_thread(_save)
            except Exception:
                pass


class AIAssistant(Extension):
    def __init__(self):
        super(AIAssistant, self).__init__()
        self.setMenuName("AI Assistant")
        self.addMenuItem("Open chat", self.showChat)
        self.addMenuItem("LLM Settings", self.showSettingsDialog)

        self.qt_bridge = QtBridge()
        self.config_store = PreferencesConfigStore(self.qt_bridge)
        self.adapter = SlicerAdapter(self.qt_bridge)
        self.registry = ToolRegistry(self.adapter)
        self.mcp = MCPHandler(self.registry)
        self.agent_service = AgentService(self.registry, self.config_store.get_snapshot)
        self._server = None

    def startServer(self, host="127.0.0.1", port=0):
        if self._server is None:
            handler_cls = create_handler(
                web_dir=WEB_DIR,
                agent_service=self.agent_service,
                mcp_handler=self.mcp,
                slicer_adapter=self.adapter,
                config_store=self.config_store
            )
            self._server = ThreadedHTTPServer((host, port), handler_cls)
            t = threading.Thread(target=self._server.serve_forever, daemon=True)
            t.start()
        return self._server.server_port

    def showChat(self):
        port = self.startServer()
        webbrowser.open("http://127.0.0.1:%d/" % port)

    def showSettingsDialog(self):
        if not HAS_QT:
            return
        snap = self.config_store.get_snapshot()
        dlg = QDialog()
        dlg.setWindowTitle("AI Assistant — Настройки LLM")
        layout = QVBoxLayout(dlg)
        form = QFormLayout()

        url_edit = QLineEdit(snap.get("base_url", ""))
        model_edit = QLineEdit(snap.get("model", ""))
        key_edit = QLineEdit("")
        key_edit.setEchoMode(QLineEdit.Password)
        key_edit.setPlaceholderText(
            "Ключ сохранён (оставьте пустым, чтобы не менять)" if snap.get("api_key") else "Введите API-ключ"
        )
        clear_cb = QCheckBox("Очистить сохранённый API-ключ")

        form.addRow("Base URL:", url_edit)
        form.addRow("Модель:", model_edit)
        form.addRow("API Key:", key_edit)
        form.addRow("", clear_cb)
        layout.addLayout(form)

        btns = QHBoxLayout()
        save_btn = QPushButton("Сохранить")
        cancel_btn = QPushButton("Отмена")
        btns.addWidget(save_btn)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

        def _on_save():
            self.config_store.update_from_dict({
                "base_url": url_edit.text(),
                "model": model_edit.text(),
                "api_key": key_edit.text(),
                "clear_api_key": clear_cb.isChecked()
            })
            dlg.accept()

        save_btn.clicked.connect(_on_save)
        cancel_btn.clicked.connect(dlg.reject)
        dlg.exec_()