"""Dialog scheduling/lifetime/Preferences with fake Qt widgets, not native keyboard acceptance."""
import importlib
import sys
import threading
import types
import unittest
from unittest import mock

import test_slicer_settings as settings
from test_agent_integration import Preferences, CONFIG
from AIAssistant.agent.config import PreferencesConfigStore
from AIAssistant.slicer.qt_bridge import QtBridge


class DialogTests(unittest.TestCase):
    worker, finish = settings.SettingsTests.worker, settings.SettingsTests.finish

    def setUp(self):
        self.app = settings.App()
        self.app.getPreferences = lambda: self.prefs
        self.prefs = Preferences(self.app)
        self.bridge = QtBridge(self.app, timeout=0.5)
        self.addCleanup(self.bridge.close)
        self.config = PreferencesConfigStore(self.bridge)
        self.config.load()
        self.config.save(**CONFIG)
        self.dialogs, self.forms, self.buttons, self.warnings = [], [], {}, []
        fixture = self

        def check():
            fixture.assertIs(threading.current_thread(), fixture.app.main, "Widget access outside Qt")

        class Signal(object):
            def __init__(self):
                self.slots = []
            def connect(self, slot):
                check()
                self.slots.append(slot)
            def emit(self, *args):
                check()
                for slot in list(self.slots):
                    slot(*args)

        class Dialog(object):
            def __init__(self):
                check()
                self.finished = Signal()
                self.native_parent = None
                self.opened, self.raised, self.activated, self.deleted = 0, 0, 0, False
                fixture.dialogs.append(self)
            def setWindowTitle(self, value):
                check()
            def setWindowModality(self, value):
                check()
                self.modality = value
            def winId(self):
                check()
                self.native_created = True
                return 1
            def windowHandle(self):
                check()
                fixture.assertTrue(self.native_created)
                return self
            def setTransientParent(self, value):
                check()
                self.native_parent = value
            def raise_(self):
                check()
                self.raised += 1
            def activateWindow(self):
                check()
                self.activated += 1
            def open(self):
                check()
                self.opened += 1
            def exec_(self):
                raise AssertionError("Nested QDialog.exec_ must not retain the QML menu call")
            def accept(self):
                self.finished.emit(1)
            def reject(self):
                self.finished.emit(0)
            def deleteLater(self):
                check()
                self.deleted = True

        class Layout(object):
            def __init__(self, parent=None):
                check()
            def addLayout(self, value):
                check()
            def addWidget(self, value):
                check()

        class Form(Layout):
            def __init__(self):
                super().__init__()
                self.fields = {}
                fixture.forms.append(self)
            def addRow(self, name, value):
                check()
                self.fields[name] = value

        class Edit(object):
            Password = 2
            def __init__(self, value):
                check()
                self.value, self.focused = value, False
            def text(self):
                check()
                return self.value
            def setText(self, value):
                check()
                self.value = value
            def setEchoMode(self, value):
                check()
                self.echo = value
            def setPlaceholderText(self, value):
                check()
                self.placeholder = value
            def clear(self):
                self.setText("")
            def setFocus(self, reason):
                check()
                self.focused = True

        class Button(object):
            def __init__(self, text):
                check()
                self.clicked = Signal()
                fixture.buttons[text] = self

        class CheckBox(object):
            def __init__(self, text):
                check()
                self.checked = False
            def isChecked(self):
                check()
                return self.checked

        core, widgets, extension = types.ModuleType("PyQt5.QtCore"), types.ModuleType("PyQt5.QtWidgets"), types.ModuleType("UM.Extension")
        core.Qt = types.SimpleNamespace(WindowModal=1, OtherFocusReason=7)
        widgets.QDialog, widgets.QVBoxLayout, widgets.QHBoxLayout, widgets.QFormLayout = Dialog, Layout, Layout, Form
        widgets.QLineEdit, widgets.QPushButton, widgets.QCheckBox = Edit, Button, CheckBox
        widgets.QMessageBox = types.SimpleNamespace(warning=lambda *args: self.warnings.append(args))
        extension.Extension = object
        patch = mock.patch.dict(sys.modules, {"UM.Extension": extension, "PyQt5": types.ModuleType("PyQt5"),
            "PyQt5.QtCore": core, "PyQt5.QtWidgets": widgets})
        patch.start()
        self.addCleanup(patch.stop)
        module_name = "AIAssistant.AIAssistant"
        self.addCleanup(lambda: sys.modules.pop(module_name, None))
        cls = importlib.import_module(module_name).AIAssistant
        self.extension = object.__new__(cls)
        self.extension._app, self.extension._config = self.app, self.config
        self.extension._adapter = types.SimpleNamespace(_bridge=self.bridge, close=self.bridge.close)
        self.extension._service = mock.Mock()
        self.extension._server, self.extension._settings_window, self.extension._closed = None, None, False
        self.main_window = object()  # The fork owner is QQuickWindow, not QWidget.
        self.app.getMainWindow = lambda: self.main_window

    def open(self):
        self.extension.showSettingsDialog()
        self.app.callbacks.get(timeout=1)()
        return self.dialogs[-1], self.forms[-1].fields

    def test_menu_returns_before_async_open_owner_focus_and_edit_save_readback(self):
        self.extension.showSettingsDialog()
        self.assertEqual(self.dialogs, [])
        self.app.callbacks.get(timeout=1)()
        dialog, fields = self.dialogs[-1], self.forms[-1].fields
        self.assertEqual(dialog.opened, 1)
        self.assertIs(dialog.native_parent, self.main_window)
        self.assertEqual(dialog.modality, 1)
        self.assertFalse(fields["Base URL"].focused)
        self.app.callbacks.get(timeout=1)()
        self.assertTrue(fields["Base URL"].focused)
        self.assertEqual(fields["API key"].text(), "")
        self.assertEqual(fields["API key"].echo, 2)
        fields["Base URL"].setText("https://example.invalid/v1")
        fields["Модель"].setText("edited-model")
        fields["API key"].setText("dummy-test-only")
        self.buttons["Сохранить"].clicked.emit()
        self.assertEqual(self.config.load(), {"base_url": "https://example.invalid/v1", "model": "edited-model", "api_key": "dummy-test-only"})
        self.assertIsNone(self.extension._settings_window)
        self.assertEqual(fields["API key"].text(), "")
        self.assertTrue(dialog.deleted)

    def test_http_worker_request_only_creates_widgets_on_qt(self):
        worker = self.worker(self.extension.showSettingsDialog)
        self.finish(worker)
        self.assertEqual(self.dialogs, [])
        self.app.callbacks.get(timeout=1)()
        self.app.callbacks.get(timeout=1)()
        self.assertTrue(self.forms[-1].fields["Base URL"].focused)

    def test_reopen_reuses_unsaved_fields_cancel_and_late_focus_are_safe(self):
        dialog, fields = self.open()
        late_focus = self.app.callbacks.get(timeout=1)
        fields["Модель"].setText("unsaved")
        self.extension.showSettingsDialog()
        self.app.callbacks.get(timeout=1)()
        self.assertEqual(len(self.dialogs), 1)
        self.assertEqual(fields["Модель"].text(), "unsaved")
        self.buttons["Отмена"].clicked.emit()
        self.assertEqual(self.config.load(), CONFIG)
        replacement, new_fields = self.open()
        late_focus()
        self.assertFalse(fields["Base URL"].focused)
        self.assertFalse(new_fields["Base URL"].focused)
        self.app.callbacks.get(timeout=1)()
        self.assertTrue(new_fields["Base URL"].focused)
        self.assertIsNot(replacement, dialog)

    def test_blank_key_preserves_clear_removes_and_no_original_secret_in_fields(self):
        dialog, fields = self.open()
        self.app.callbacks.get(timeout=1)()
        self.assertEqual(fields["API key"].text(), "")
        self.assertNotIn(CONFIG["api_key"], fields["API key"].placeholder)
        self.buttons["Сохранить"].clicked.emit()
        self.assertEqual(self.config.load(), CONFIG)
        dialog, fields = self.open()
        self.app.callbacks.get(timeout=1)()
        fields[""].checked = True
        self.buttons["Сохранить"].clicked.emit()
        self.assertEqual(self.config.load()["api_key"], "")

    def test_invalid_save_keeps_editable_dialog_no_preferences_write_or_secret_warning(self):
        dialog, fields = self.open()
        self.app.callbacks.get(timeout=1)()
        before = list(self.prefs.writes)
        fields["Base URL"].setText("file:///invalid")
        fields["API key"].setText("secret-in-field")
        self.buttons["Сохранить"].clicked.emit()
        self.assertIs(self.extension._settings_window, dialog)
        self.assertEqual(self.prefs.writes, before)
        self.assertNotIn("secret-in-field", str(self.warnings))
        fields["Base URL"].setText("https://example.invalid/v1")
        self.buttons["Сохранить"].clicked.emit()
        self.assertTrue(dialog.deleted)

    def test_shutdown_closes_retained_dialog_and_queued_menu_or_focus_does_not_reopen(self):
        dialog, fields = self.open()
        fields["API key"].setText("unsaved-secret")
        self.extension.showSettingsDialog()  # A second menu callback is still queued.
        self.extension.close()
        self.buttons["Сохранить"].clicked.emit()  # Late delivery must not touch closed Preferences/bridge.
        self.assertTrue(dialog.deleted)
        self.assertEqual(fields["API key"].text(), "")
        self.assertIsNone(self.extension._settings_window)
        while not self.app.callbacks.empty():
            self.app.callbacks.get_nowait()()
        self.assertEqual(len(self.dialogs), 1)
        self.assertEqual(self.config.snapshot(), CONFIG)
        self.extension._service.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
