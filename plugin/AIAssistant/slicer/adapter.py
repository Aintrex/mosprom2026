import logging
import math
import threading
import time
import traceback

from .qt_bridge import QtBridge, SlicerError
from .modes import PRINT_MODES, PrintModeSwitch, get_slicer_state, validate_mode_arguments
from .profiles import (ProfileSelection, MaterialSelection, list_quality_profiles,
                       list_materials, validate_profile_arguments)
from .models import ModelRegistry, ModelLoad, checked_model_path, validate_model_arguments


TEST_MACHINE_PROFILE = "Stereotech Hybrid 530 V5.2.4"
LOGGER = logging.getLogger(__name__)


def is_finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_setting_arguments(arguments, write=False):
    required = {"key", "scope", "value"} if write else {"key", "scope"}
    if not isinstance(arguments, dict) or set(arguments) != required:
        raise ValueError("Укажите только обязательные поля: %s" % ", ".join(sorted(required)))
    if arguments["key"] != "layer_height":
        raise ValueError("Поддерживается только key=layer_height")
    if arguments["scope"] != "global":
        raise ValueError("Поддерживается только scope=global")
    if write and (not is_finite_number(arguments["value"]) or arguments["value"] <= 0):
        raise ValueError("value должно быть конечным числом больше нуля (мм)")


class SlicerAdapter(object):
    def __init__(self, app=None, timeout=5.0, load_timeout=10.0):
        # Constructed by Extension on the Qt thread; dev mode has no app.
        self._bridge = QtBridge(app, timeout) if app is not None else None
        self._timeout = timeout
        self._change_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._load_timeout = load_timeout
        self._models = ModelRegistry()

    def close(self):
        if self._shutdown.is_set():
            return
        self._shutdown.set()
        if self._bridge is not None:
            try:
                self._bridge.run(self._models.close)
            except Exception as exc:
                LOGGER.error("Model shutdown cleanup: %s", type(exc).__name__)
            self._bridge.close()

    def get_setting(self, key, scope):
        validate_setting_arguments({"key": key, "scope": scope})
        return self._run(self._get_setting)

    def set_setting(self, key, scope, value):
        validate_setting_arguments({"key": key, "scope": scope, "value": value}, write=True)
        return self._run(lambda app: self._set_setting(app, value), write=True)

    def get_slicer_state(self):
        return self._run(get_slicer_state)

    def set_print_mode(self, mode):
        validate_mode_arguments({"mode": mode})
        return self._run(lambda bridge: self._switch_mode(bridge, mode), write=True, worker=True)

    def list_quality_profiles(self):
        return self._run(list_quality_profiles)

    def list_materials(self, extruder_id):
        validate_profile_arguments("list_materials", {"extruder_id": extruder_id})
        return self._run(lambda app: list_materials(app, extruder_id))

    def select_profile(self, quality_type):
        validate_profile_arguments("select_profile", {"quality_type": quality_type})
        return self._run(lambda bridge: self._select(bridge, ProfileSelection(quality_type)),
                         write=True, worker=True, selection=True)

    def select_material(self, extruder_id, root_material_id):
        validate_profile_arguments("select_material", {"extruder_id": extruder_id, "root_material_id": root_material_id})
        return self._run(lambda bridge: self._select(bridge, MaterialSelection(extruder_id, root_material_id)),
                         write=True, worker=True, selection=True)

    def list_models(self):
        return self._run(self._models.list_models)

    def get_model_bounds(self, object_id):
        validate_model_arguments("get_model_bounds", {"object_id": object_id})
        return self._run(lambda app: self._models.get_bounds(app, object_id))

    def load_model(self, path):
        validate_model_arguments("load_model", {"path": path})
        return self._run(lambda bridge: self._load_model(bridge, path), worker=True, model_loading=True)

    def _load_model(self, bridge, path):
        bridge.ensure_open()
        if bridge.is_main_thread():
            raise SlicerError("slicer_error", "Импорт модели требуется вызывать из HTTP worker. Запуск не выполнен.")
        path = checked_model_path(path)
        if not self._change_lock.acquire(False):
            raise SlicerError("model_load_busy", "Занят импорт, режим или профиль. Незавершённый импорт после timeout требует ожидания; если fileCompleted не придёт, перезапустите плагин/приложение.")
        operation = ModelLoad(self._models, path, self._change_lock)
        deadline = time.monotonic() + self._load_timeout
        try:
            bridge.run(operation.start, timeout=min(self._timeout, max(0, deadline - time.monotonic())))
            while True:
                bridge.ensure_open()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SlicerError("model_load_timeout", "Импорт запущен, но завершение за 10 секунд не подтверждено. Штатный job не отменён и может добавить модель позже.")
                result = bridge.run(operation.check, timeout=min(self._timeout, remaining))
                if result is not None:
                    return result
                self._shutdown.wait(min(0.05, max(0, deadline - time.monotonic())))
        finally:
            # После запуска блокировку освободит свой fileCompleted или shutdown,
            # даже если HTTP worker уже вернул timeout/ошибку.
            operation.abandon()

    def _select(self, bridge, operation):
        bridge.ensure_open()
        if bridge.is_main_thread():
            raise SlicerError("slicer_error", "Выбор профиля/материала требует HTTP worker и свободного Qt event loop. Запись не начата.")
        if not self._change_lock.acquire(False):
            raise SlicerError("selection_busy", "Уже меняется профиль, материал или режим; дождитесь результата и перечитайте списки")
        try:
            deadline = time.monotonic() + self._timeout
            result = bridge.run(operation.start, timeout=max(0, deadline - time.monotonic()))
            while result is None:
                bridge.ensure_open()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SlicerError("selection_not_applied", "STE Slicer не подтвердил выбранные контейнеры за отведённое время")
                result = bridge.run(operation.check, timeout=remaining)
                if result is None:
                    self._shutdown.wait(min(0.05, max(0, deadline - time.monotonic())))
            bridge.ensure_open()
            return result
        finally:
            self._change_lock.release()

    def _switch_mode(self, bridge, mode):
        bridge.ensure_open()
        if bridge.is_main_thread():
            raise SlicerError("slicer_error", "Переключайте режим из HTTP worker: подтверждение требует свободного Qt event loop. Запись не начата.")
        if not self._change_lock.acquire(False):
            raise SlicerError("switch_in_progress", "Уже выполняется переключение режима; дождитесь результата и вызовите get_slicer_state")
        try:
            deadline = time.monotonic() + self._timeout
            switch = PrintModeSwitch(mode)
            result = bridge.run(switch.start, timeout=max(0, deadline - time.monotonic()))
            while True:
                bridge.ensure_open()
                if result is not None and result["after"] == mode and result["backend"]["matches_mode"]:
                    return result
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if result is not None and result["after"] != mode:
                        raise SlicerError("mode_not_applied", "STE Slicer не подтвердил значение printing_mode за отведённое время")
                    raise SlicerError("backend_not_switched", "Настройка режима не подтверждена выбранным backend за отведённое время")
                if result is not None:
                    # Только worker ждёт; shutdown прерывает паузу.
                    self._shutdown.wait(min(0.05, remaining))
                    if time.monotonic() >= deadline:
                        continue
                bridge.ensure_open()
                result = bridge.run(switch.check, timeout=max(0, deadline - time.monotonic()))
        finally:
            self._change_lock.release()

    def _run(self, operation, write=False, worker=False, selection=False, model_loading=False):
        try:
            if self._bridge is None:
                raise SlicerError("slicer_not_connected", "Нет подключения к STE Slicer. Запустите инструмент из плагина.")
            data = operation(self._bridge) if worker else self._bridge.run(operation)
            return {"ok": True, "data": data}
        except SlicerError as exc:
            message = exc.message
            if model_loading and exc.code in ("qt_timeout", "model_load_timeout", "model_scene_changed", "model_load_failed", "slicer_error", "slicer_not_connected"):
                message += " Вызовите list_models; после начатого импорта состояние может измениться позже. Автоматического повтора или удаления нет."
            if selection and exc.code not in ("custom_profile_active", "user_changes_present", "selection_busy", "extruder_not_found", "no_active_machine"):
                message += " Перечитайте list_quality_profiles, list_materials и get_slicer_state. Если запись началась, состояние могло измениться; автоматического отката/повтора нет."
            if worker and exc.code in ("qt_timeout", "mode_not_applied", "backend_not_switched", "backend_unavailable", "slicer_error"):
                message += " Вызовите get_slicer_state: состояние могло измениться. Откат не выполнялся; не повторяйте запись автоматически."
            if worker and exc.code == "slicer_not_connected" and self._bridge is not None:
                message += " Если переключение уже началось, состояние могло измениться. После подключения вызовите get_slicer_state."
            return {"ok": False, "error": {"code": exc.code, "message": message}}
        except Exception as exc:
            # Log traceback frames and type, excluding possibly private API values.
            LOGGER.error("STE settings API: %s\n%s", type(exc).__name__,
                         "".join(traceback.format_tb(exc.__traceback__)))
            message = "Ошибка API STE Slicer. Проверьте журнал приложения."
            if write:
                message += (" Запись могла начаться; вызовите get_slicer_state перед дальнейшими действиями."
                            if worker else " Запись могла начаться; перечитайте настройку перед дальнейшими действиями.")
            if selection:
                message += " Перечитайте list_quality_profiles и list_materials; откат не выполнялся."
            if model_loading:
                message += " Вызовите list_models. Если job запущен, он может завершиться позже; не повторяйте импорт автоматически."
            return {"ok": False, "error": {"code": "slicer_error", "message": message}}

    @staticmethod
    def _stack(app):
        stack = app.getGlobalContainerStack()
        if stack is None:
            raise SlicerError("no_active_machine", "В STE Slicer не выбрана активная машина")
        if not stack.hasProperty("layer_height", "value"):
            raise SlicerError("setting_unavailable", "Активная машина не поддерживает layer_height")
        return stack

    @staticmethod
    def _value(stack):
        value = stack.getProperty("layer_height", "value")
        if not is_finite_number(value):
            raise SlicerError("setting_unavailable", "STE Slicer не вернул числовое значение layer_height")
        return float(value)

    def _get_setting(self, app):
        return {"key": "layer_height", "scope": "global",
                "value": self._value(self._stack(app)), "unit": "mm"}

    def _set_setting(self, app, value):
        stack = self._stack(app)
        before = self._value(stack)
        # getProperty evaluates SettingFunction against the current stack.
        # Warning bounds intentionally do not prohibit the change.
        for property_name, is_minimum in (("minimum_value", True), ("maximum_value", False)):
            bound = stack.getProperty("layer_height", property_name)
            if bound is None:
                continue
            if not is_finite_number(bound):
                raise SlicerError("slicer_error", "Не удалось прочитать границу layer_height. Проверьте профиль машины.")
            if (is_minimum and value < bound) or (not is_minimum and value > bound):
                raise SlicerError("setting_out_of_range", "layer_height не соответствует %s=%s мм активного профиля" % (property_name, bound))
        changes = stack.userChanges
        if changes is None:
            raise SlicerError("setting_unavailable", "Нет контейнера пользовательских настроек активной машины")
        changes.setProperty("layer_height", "value", value)
        after = self._value(stack)
        if not math.isclose(after, value, rel_tol=1e-9, abs_tol=1e-12):
            raise SlicerError("setting_not_applied", "STE Slicer не подтвердил запрошенное значение. Перечитайте layer_height и проверьте профиль.")
        return {"key": "layer_height", "scope": "global", "before": before,
                "after": after, "unit": "mm"}
