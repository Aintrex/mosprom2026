import threading
import webbrowser
import logging

from UM.Extension import Extension

from .agent.service import AgentService
from .agent.config import PreferencesConfigStore
from .server import create_server
from .slicer.adapter import SlicerAdapter


class AIAssistant(Extension):
    def __init__(self, app):
        super().__init__()
        self._app = app
        self._server = None
        self._settings_window = None
        self._adapter = SlicerAdapter(app)
        self._config = PreferencesConfigStore(self._adapter._bridge)
        try:
            self._config.load()
        except Exception as exc:
            logging.getLogger(__name__).error("LLM Preferences load: %s", type(exc).__name__)
        self._service = AgentService(self._adapter, config_provider=self._config.snapshot)
        self._closed = False
        self.setMenuName("AI Assistant")
        self.addMenuItem("Open chat", self.showChat)
        self.addMenuItem("LLM Settings", self.showSettingsDialog)
        app.applicationShuttingDown.connect(self.close)

    def showChat(self):
        if self._closed:
            return
        if self._server is None:
            self._server = create_server(self._service)
            thread = threading.Thread(target=self._server.serve_forever)
            thread.daemon = True
            thread.start()
        webbrowser.open("http://127.0.0.1:%d/" % self._server.server_port)

    def close(self, *args):
        if self._closed:
            return
        self._closed = True
        if self._settings_window is not None:
            dialog = self._settings_window
            try:
                self._adapter._bridge.run(lambda app: dialog.reject() if self._settings_window is dialog else None)
            except Exception as exc:
                logging.getLogger(__name__).error("LLM Settings close: %s", type(exc).__name__)
        self._service.close()
        self._adapter.close()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def showSettingsDialog(self):
        if not self._closed:
            # Return to the QML menu before opening a widget; release its input grab.
            self._app.callLater(self._show_settings_on_qt)

    def _show_settings_on_qt(self):
        if not self._closed:
            self._adapter._bridge.run(self._settings_dialog)

    def _settings_dialog(self, app):
        # UI adapted from artem_backend b9c0190; Preferences use our QtBridge.
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLineEdit,
                                     QPushButton, QHBoxLayout, QCheckBox, QMessageBox)
        from PyQt5.QtCore import Qt
        if self._settings_window is not None:
            self._settings_window.raise_()
            self._settings_window.activateWindow()
            return
        try:
            config = self._config.load()
        except Exception as exc:
            logging.getLogger(__name__).error("LLM Settings read: %s", type(exc).__name__)
            QMessageBox.warning(None, "AI Assistant", "Preferences не прочитаны. Проверьте журнал; сохранение не начато.")
            return
        dialog = QDialog()
        dialog.setWindowTitle("AI Assistant — LLM Settings")
        # MainWindow in this fork is QQuickWindow, not a QWidget constructor parent.
        dialog.setWindowModality(Qt.WindowModal)
        dialog.winId()
        dialog.windowHandle().setTransientParent(app.getMainWindow())
        layout, form = QVBoxLayout(dialog), QFormLayout()
        url, model, key = QLineEdit(config["base_url"]), QLineEdit(config["model"]), QLineEdit("")
        key.setEchoMode(QLineEdit.Password)
        key.setPlaceholderText("Пусто — не менять сохранённый ключ" if config["api_key"] else "Введите ключ")
        clear = QCheckBox("Удалить сохранённый ключ")
        for label, widget in (("Base URL", url), ("Модель", model), ("API key", key), ("", clear)):
            form.addRow(label, widget)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        save, cancel = QPushButton("Сохранить"), QPushButton("Отмена")
        buttons.addWidget(save)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

        def on_save():
            if self._closed or self._settings_window is not dialog:
                return
            try:
                self._config.save(url.text(), model.text(), key.text(), clear.isChecked())
            except Exception as exc:
                logging.getLogger(__name__).error("LLM Settings save: %s", type(exc).__name__)
                QMessageBox.warning(dialog, "AI Assistant", "Сохранение не подтверждено. Перечитайте Preferences перед следующей попыткой; автоматического отката нет.")
                return
            dialog.accept()
        save.clicked.connect(on_save)
        cancel.clicked.connect(dialog.reject)

        def on_finished(result):
            key.clear()
            if self._settings_window is dialog:
                self._settings_window = None
            dialog.deleteLater()

        def focus_url():
            if not self._closed and self._settings_window is dialog:
                dialog.raise_()
                dialog.activateWindow()
                url.setFocus(Qt.OtherFocusReason)

        dialog.finished.connect(on_finished)
        self._settings_window = dialog  # open() returns immediately; retain until finished.
        dialog.open()
        app.callLater(focus_url)
