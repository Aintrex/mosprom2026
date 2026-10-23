import logging
import math
import traceback

from .qt_bridge import QtBridge, SlicerError


PRINT_MODES = ("classic", "cylindrical", "cylindrical_full")
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
    def __init__(self, app=None, timeout=5.0):
        # Constructed by Extension on the Qt thread; dev mode has no app.
        self._bridge = QtBridge(app, timeout) if app is not None else None

    def close(self):
        if self._bridge is not None:
            self._bridge.close()

    def get_setting(self, key, scope):
        validate_setting_arguments({"key": key, "scope": scope})
        return self._run(self._get_setting)

    def set_setting(self, key, scope, value):
        validate_setting_arguments({"key": key, "scope": scope, "value": value}, write=True)
        return self._run(lambda app: self._set_setting(app, value), write=True)

    def _run(self, operation, write=False):
        try:
            if self._bridge is None:
                raise SlicerError("slicer_not_connected", "Нет подключения к STE Slicer. Запустите инструмент из плагина.")
            return {"ok": True, "data": self._bridge.run(operation)}
        except SlicerError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
        except Exception as exc:
            # Log traceback frames and type, excluding possibly private API values.
            LOGGER.error("STE settings API: %s\n%s", type(exc).__name__,
                         "".join(traceback.format_tb(exc.__traceback__)))
            message = "Ошибка API STE Slicer. Проверьте журнал приложения."
            if write:
                message += " Запись могла начаться; перечитайте настройку перед дальнейшими действиями."
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
