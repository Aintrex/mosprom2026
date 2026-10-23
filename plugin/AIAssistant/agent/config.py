"""Preferences через существующий QtBridge; ключ доступен только Python."""
import logging
import threading
from urllib.parse import urlsplit

from ..slicer.qt_bridge import SlicerError


FIELDS = ("base_url", "model", "api_key")


def validate_config(config):
    if any(not isinstance(config.get(key), str) for key in FIELDS):
        raise ValueError("Настройки LLM должны быть строками")
    if any(len(config[key]) > 4096 or any(c in config[key] for c in ("\r", "\n", "\x00")) for key in FIELDS):
        raise ValueError("Недопустимый формат настроек LLM")
    if config["base_url"]:
        try:
            url = urlsplit(config["base_url"])
            valid = (url.scheme in ("http", "https") and url.hostname and url.port != 0 and
                     not url.username and not url.password and not url.query and not url.fragment and
                     "?" not in config["base_url"] and "#" not in config["base_url"])
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Нужен HTTP(S) Base URL без credentials, query и fragment")


class PreferencesConfigStore(object):
    PREFIX = "ai_assistant/"

    def __init__(self, bridge):
        self.bridge = bridge
        self.lock, self.write_lock = threading.Lock(), threading.Lock()
        self.cache = {key: "" for key in FIELDS}
        self.confirmed = False

    def _read(self, app):
        prefs = app.getPreferences()
        for key in FIELDS:
            prefs.addPreference(self.PREFIX + key, "")
        result = {key: prefs.getValue(self.PREFIX + key) for key in FIELDS}
        validate_config(result)
        return result

    def _run(self, operation):
        try:
            return self.bridge.run(operation)
        except SlicerError:
            raise
        except Exception as exc:
            logging.getLogger(__name__).error("LLM Preferences: %s", type(exc).__name__)
            raise SlicerError("llm_config_failed", "Preferences не подтвердили операцию. Перечитайте настройки; автоматического повтора нет")

    def load(self):
        if self.bridge is None:
            raise SlicerError("slicer_not_connected", "Preferences доступны в STE Slicer")
        if not self.write_lock.acquire(False):
            raise SlicerError("llm_config_busy", "Сохранение ещё не подтверждено; дождитесь завершения")
        try:
            with self.lock:
                self.confirmed = False
            result = self._run(self._read)
            with self.lock:
                self.cache, self.confirmed = dict(result), True
            return self.snapshot()
        finally:
            self.write_lock.release()

    def snapshot(self):
        with self.lock:
            if not self.confirmed:
                raise SlicerError("llm_config_unconfirmed", "Настройки LLM не подтверждены; откройте LLM Settings и перечитайте Preferences")
            return dict(self.cache)

    def save(self, base_url, model, api_key="", clear_api_key=False):
        if not isinstance(clear_api_key, bool):
            raise ValueError("clear_api_key должен быть bool")
        candidate = {"base_url": base_url, "model": model, "api_key": api_key}
        validate_config(candidate)
        if self.bridge is None:
            raise SlicerError("slicer_not_connected", "Нет Preferences STE Slicer")
        if not self.write_lock.acquire(False):
            raise SlicerError("llm_config_busy", "Уже сохраняются настройки LLM")
        try:
            with self.lock:
                self.confirmed = False

            def write(app):
                before = self._read(app)
                after = {"base_url": base_url.strip(), "model": model.strip(),
                         "api_key": "" if clear_api_key else api_key.strip() or before["api_key"]}
                prefs = app.getPreferences()
                for key in FIELDS:
                    prefs.setValue(self.PREFIX + key, after[key])
                actual = self._read(app)
                if actual != after:
                    raise SlicerError("llm_config_not_applied", "Preferences не подтвердили сохранение. Перечитайте настройки; автоматического отката нет")
                return actual
            actual = self._run(write)
            with self.lock:
                self.cache, self.confirmed = dict(actual), True
            return {"configured": bool(all(actual.values())), "has_key": bool(actual["api_key"])}
        finally:
            self.write_lock.release()
