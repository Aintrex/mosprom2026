import logging
import threading
import time
import traceback

from .qt_bridge import QtBridge, SlicerError
from .modes import PRINT_MODES, PrintModeSwitch, get_slicer_state, validate_mode_arguments
from .profiles import (ProfileSelection, MaterialSelection, list_quality_profiles,
                       list_materials, validate_profile_arguments)
from .models import ModelRegistry, ModelLoad, checked_model_path, validate_model_arguments
from .slicing import Slicing, validate_slice_arguments
from .exports import Exports, validate_export_arguments
from . import settings
from .transforms import ModelTransforms, validate_transform_arguments
from .arrangement import Arrangement, validate_arrangement_arguments
from .custom_profiles import CustomProfile, list_custom_profiles, validate_custom_arguments
from .settings import validate_setting_arguments, finite as is_finite_number


TEST_MACHINE_PROFILE = "Stereotech Hybrid 530 V5.2.4"
LOGGER = logging.getLogger(__name__)


_UNSET = object()


class SlicerAdapter(object):
    def __init__(self, app=None, timeout=5.0, load_timeout=60.0, slicing_timeout=180.0, export_timeout=120.0, arrangement_timeout=120.0):
        # Constructed by Extension on the Qt thread; dev mode has no app.
        self._bridge = QtBridge(app, timeout) if app is not None else None
        self._timeout = timeout
        self._change_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._load_timeout = load_timeout
        self._models = ModelRegistry()
        self._slicing = Slicing(self._change_lock, slicing_timeout)
        self._exports = Exports(self._slicing, self._change_lock, export_timeout)
        self._transforms = ModelTransforms(self._models, self._change_lock, self._slicing.invalidate)
        self._arrangement = Arrangement(self._models, self._transforms, self._slicing, self._change_lock, arrangement_timeout)

    def close(self):
        if self._shutdown.is_set():
            return
        self._shutdown.set()
        if self._bridge is not None:
            try:
                self._bridge.run(self._close_operations)
            except Exception as exc:
                LOGGER.error("Slicer shutdown cleanup: %s", type(exc).__name__)
            self._bridge.close()

    def _close_operations(self, app):
        try:
            self._arrangement.close(app)
        finally:
            try:
                self._exports.close(app)
            finally:
                try:
                    self._slicing.close(app)
                finally:
                    self._models.close(app)

    @staticmethod
    def _setting_arguments(arguments, extruder_id):
        if extruder_id is not _UNSET:
            arguments["extruder_id"] = extruder_id
        return arguments

    def get_setting(self, key, scope, extruder_id=_UNSET):
        arguments = self._setting_arguments({"key": key, "scope": scope}, extruder_id)
        validate_setting_arguments(arguments)
        return self._run(lambda app: settings.get_setting(app, **arguments))

    def set_setting(self, key, scope, value, extruder_id=_UNSET):
        arguments = self._setting_arguments({"key": key, "scope": scope, "value": value}, extruder_id)
        validate_setting_arguments(arguments, write=True)
        return self._run(lambda bridge: self._write_setting(bridge, arguments), write=True, worker=True)

    def describe_settings(self, scope, extruder_id=_UNSET, keys=_UNSET, category=_UNSET, query=_UNSET,
                          offset=_UNSET, limit=_UNSET):
        arguments = self._setting_arguments({"scope": scope}, extruder_id)
        for name, value in (("keys", keys), ("category", category), ("query", query), ("offset", offset), ("limit", limit)):
            if value is not _UNSET:
                arguments[name] = value
        validate_setting_arguments(arguments, describe=True)
        return self._run(lambda app: settings.describe_settings(app, **arguments))

    def _write_setting(self, bridge, arguments):
        if not self._change_lock.acquire(False):
            raise SlicerError("slicing_busy", "Занята нарезка, импорт или смена параметров; дождитесь завершения")
        try:
            return bridge.run(lambda app: settings.set_setting(app, self._slicing.invalidate, **arguments))
        finally:
            self._change_lock.release()

    def start_slicing(self):
        return self._run(self._slicing.start, write=True)

    def get_slicing_status(self, slice_id):
        validate_slice_arguments("get_slicing_status", {"slice_id": slice_id})
        return self._run(lambda app: self._slicing.status(app, slice_id))

    def get_slicing_result(self, slice_id):
        validate_slice_arguments("get_slicing_result", {"slice_id": slice_id})
        return self._run(lambda app: self._slicing.result(app, slice_id))

    def export_gcode(self, slice_id):
        validate_export_arguments("export_gcode", {"slice_id": slice_id})
        return self._run(lambda app: self._exports.start(app, slice_id, "gcode"), write=True)

    def save_project_3mf(self, slice_id):
        validate_export_arguments("save_project_3mf", {"slice_id": slice_id})
        return self._run(lambda app: self._exports.start(app, slice_id, "project"), write=True)

    def get_export_status(self, export_id):
        validate_export_arguments("get_export_status", {"export_id": export_id})
        return self._run(lambda app: self._exports.status(app, export_id))

    def resolve_artifact(self, artifact_id):
        # Internal boundary, deliberately absent from ToolRegistry/MCP/LLM.
        if not isinstance(artifact_id, str) or not artifact_id or len(artifact_id) > 128:
            raise ValueError("Нужен ограниченный artifact_id")
        return self._run(lambda app: self._exports.resolve_artifact(app, artifact_id))

    def get_slicer_state(self):
        return self._run(get_slicer_state)

    def set_print_mode(self, mode):
        validate_mode_arguments({"mode": mode})
        return self._run(lambda bridge: self._switch_mode(bridge, mode), write=True, worker=True)

    def list_quality_profiles(self):
        return self._run(list_quality_profiles)

    def list_custom_profiles(self):
        return self._run(list_custom_profiles)

    def create_custom_profile(self, name):
        return self._custom_result("create", {"name": name})

    def select_custom_profile(self, profile_id):
        return self._custom_result("select", {"profile_id": profile_id})

    def save_custom_profile(self, profile_id):
        return self._custom_result("save", {"profile_id": profile_id})

    def _custom_result(self, action, arguments):
        validate_custom_arguments(action + "_custom_profile", arguments)
        operation = CustomProfile(action, next(iter(arguments.values())), self._slicing.invalidate)
        result = self._run(lambda bridge: self._custom_profile(bridge, operation), write=True, worker=True)
        if not result.get("ok"):
            result["error"]["actual"] = dict(operation.actual)
            result["error"]["message"] += " Перечитайте list_custom_profiles и UI; автоматического повтора/отката нет."
        return result

    def _custom_profile(self, bridge, operation):
        bridge.ensure_open()
        if bridge.is_main_thread():
            raise SlicerError("slicer_error", "Профили требуется вызывать из HTTP worker; операция не начата")
        if not self._change_lock.acquire(False):
            raise SlicerError("selection_busy", "Занят импорт, нарезка, экспорт, расстановка или профиль")
        guard, state = threading.Lock(), {"active": False, "released": False}
        deadline = time.monotonic() + self._timeout

        def release():
            if not state["released"]:
                state["released"] = True
                self._change_lock.release()

        def call(callback):
            bridge.ensure_open()
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._shutdown.is_set():
                raise SlicerError("custom_profile_unconfirmed", "Истёк бюджет подтверждения профиля; изменения могли сохраниться, перечитайте list_custom_profiles/UI без повтора")
            def invoke(app):
                with guard:
                    operation._live()
                    state["active"] = True
                try:
                    return callback(app)
                finally:
                    with guard:
                        state["active"] = False
                        if operation.stop.is_set():
                            release()
            return bridge.run(invoke, timeout=remaining)

        try:
            result = call(operation.start)
            while result is None:
                result = call(operation.check)
                if result is None:
                    self._shutdown.wait(min(0.05, max(0, deadline - time.monotonic())))
            if "disk_plan" in result:
                plan = result["disk_plan"]  # Strings only, never Qt objects in filesystem worker.
                operation.verify_files(plan)
                return call(lambda app: operation.finish(app, plan))
            return result["result"]
        finally:
            with guard:
                operation.stop.set()
                # Native provider may still be inside a timed-out Qt callback; it releases on exit.
                if not state["active"]:
                    release()

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

    def get_model_state(self, object_id):
        validate_transform_arguments("get_model_state", {"object_id": object_id})
        return self._run(lambda app: self._transforms.state(app, object_id))

    def arrange_models(self):
        deadline = time.monotonic() + self._timeout
        return self._run(lambda app: self._arrangement.start(app, deadline), write=True)

    def get_arrangement_status(self, arrangement_id):
        validate_arrangement_arguments("get_arrangement_status", {"arrangement_id": arrangement_id})
        return self._run(lambda app: self._arrangement.status(app, arrangement_id))

    def move_model(self, object_id, position_mm):
        return self._model_operation("move_model", {"object_id": object_id, "position_mm": position_mm})

    def rotate_model(self, object_id, axis, angle_degrees):
        return self._model_operation("rotate_model", {"object_id": object_id, "axis": axis, "angle_degrees": angle_degrees})

    def scale_model(self, object_id, factors):
        return self._model_operation("scale_model", {"object_id": object_id, "factors": factors})

    def delete_model(self, object_id):
        return self._model_operation("delete_model", {"object_id": object_id})

    def _model_operation(self, name, arguments):
        validate_transform_arguments(name, arguments)
        return self._run(lambda app: self._transforms.apply(app, name, arguments), write=True, model_transform=True)

    def load_model(self, path, reader_done=None):
        # Internal callback only; once handed to ModelLoad, HTTP timeout cannot release it.
        lifetime = [reader_done]
        try:
            validate_model_arguments("load_model", {"path": path})
            return self._run(lambda bridge: self._load_model(bridge, path, lifetime), worker=True, model_loading=True)
        finally:
            if lifetime[0] is not None:
                lifetime[0]()

    def _load_model(self, bridge, path, lifetime):
        bridge.ensure_open()
        if bridge.is_main_thread():
            raise SlicerError("slicer_error", "Импорт модели требуется вызывать из HTTP worker. Запуск не выполнен.")
        path = checked_model_path(path)
        if not self._change_lock.acquire(False):
            raise SlicerError("model_load_busy", "Занят импорт, режим или профиль. Незавершённый импорт после timeout требует ожидания; если fileCompleted не придёт, перезапустите плагин/приложение.")
        operation = ModelLoad(self._models, path, self._change_lock, lifetime[0])
        lifetime[0] = None
        deadline = time.monotonic() + self._load_timeout
        try:
            bridge.run(operation.start, timeout=min(self._timeout, max(0, deadline - time.monotonic())))
            while True:
                bridge.ensure_open()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SlicerError("model_load_timeout", "Импорт запущен, но завершение за %d секунд не подтверждено." % self._load_timeout + " Штатный job не отменён и может добавить модель позже.")
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

    def _run(self, operation, write=False, worker=False, selection=False, model_loading=False, model_transform=False):
        try:
            if self._bridge is None:
                raise SlicerError("slicer_not_connected", "Нет подключения к STE Slicer. Запустите инструмент из плагина.")
            data = operation(self._bridge) if worker else self._bridge.run(operation)
            return {"ok": True, "data": data}
        except SlicerError as exc:
            message = exc.message
            if model_transform and exc.code in ("qt_timeout", "model_transform_not_applied", "slicer_not_connected"):
                message += " Перечитайте get_model_state/list_models и UI. После начала операции исход может быть неопределён; автоматического повтора/отката нет."
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
            if model_transform:
                message += " Перечитайте get_model_state/list_models и UI; автоматического повтора/отката нет."
            return {"ok": False, "error": {"code": "slicer_error", "message": message}}
