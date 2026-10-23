"""Короткие операции Qt; ожидание переключения выполняет worker адаптера."""
from .qt_bridge import SlicerError


PRINT_MODES = ("classic", "cylindrical", "cylindrical_full")


def validate_mode_arguments(arguments):
    if (not isinstance(arguments, dict) or set(arguments) != {"mode"} or
            not isinstance(arguments["mode"], str) or arguments["mode"] not in PRINT_MODES):
        raise ValueError("Укажите только mode: classic, cylindrical или cylindrical_full")


def _text(value):
    if value is not None and not isinstance(value, str):
        raise SlicerError("slicer_error", "STE Slicer вернул неверный формат идентификатора или режима")
    return value


def _backend_data(current, expected):
    return {"id": _text(current.getPluginId()) if current is not None else None,
            "expected_id": _text(expected.getPluginId()) if expected is not None else None,
            "matches_mode": current is not None and expected is not None and current is expected}


def _options(stack):
    if not stack.hasProperty("printing_mode", "value"):
        return {}
    if stack.getProperty("printing_mode", "type") != "enum":
        return {}
    options = stack.getProperty("printing_mode", "options")
    return options if isinstance(options, dict) else {}


def _supported(stack, options, mode):
    return (mode in options and
            (mode == "classic" or stack.getProperty("machine_five_axis", "value") is True))


def get_slicer_state(app):
    state = {"connected": True, "application_version": _text(app.getVersion()),
             "machine": None, "printing_mode": None, "backend": None, "available_modes": []}
    stack = app.getGlobalContainerStack()
    if stack is None:
        return state
    definition = stack.getDefinition()
    state["machine"] = {"id": _text(stack.getId()), "name": _text(stack.getName()),
                        "definition_id": _text(definition.getId()) if definition is not None else None}
    mode = _text(stack.getProperty("printing_mode", "value"))
    state["printing_mode"] = mode
    manager = app.getBackendManager()
    current = manager.getCurrentBackend() if manager is not None else None
    expected = manager.getBackendByType(mode) if manager is not None and mode is not None else None
    state["backend"] = _backend_data(current, expected)
    options = _options(stack)
    for candidate in PRINT_MODES:
        if _supported(stack, options, candidate):
            state["available_modes"].append({
                "id": candidate, "label": _text(options[candidate]),
                "backend_available": manager is not None and manager.getBackendByType(candidate) is not None})
    return state


class PrintModeSwitch(object):
    """Ссылки UM остаются внутри операций Qt, наружу выходят только данные."""

    def __init__(self, mode):
        self.mode = mode
        self.stack = None
        self.manager = None
        self.expected = None
        self.before = None
        self.before_backend = None

    def start(self, app):
        self.stack = app.getGlobalContainerStack()
        if self.stack is None:
            raise SlicerError("no_active_machine", "В STE Slicer не выбрана активная машина")
        if not self.stack.hasProperty("printing_mode", "value"):
            raise SlicerError("setting_unavailable", "Активная машина не поддерживает printing_mode")
        options = _options(self.stack)
        if not _supported(self.stack, options, self.mode):
            raise SlicerError("mode_unavailable", "Запрошенный режим недоступен в определении активной машины")
        self.manager = app.getBackendManager()
        self.expected = self.manager.getBackendByType(self.mode) if self.manager is not None else None
        if self.expected is None:
            raise SlicerError("backend_unavailable", "Для запрошенного режима не зарегистрирован backend STE Slicer")
        self.before = _text(self.stack.getProperty("printing_mode", "value"))
        self.before_backend = self.manager.getCurrentBackend()
        if self.before != self.mode:
            changes = self.stack.userChanges
            if changes is None:
                raise SlicerError("setting_unavailable", "Нет контейнера пользовательских настроек активной машины")
            changes.setProperty("printing_mode", "value", self.mode)
            # Подтверждение — отдельным событием после штатных queued-сигналов.
            return None
        # В согласованном режиме не пишем value и не перезапускаем backend.
        return self.check(app)

    def check(self, app):
        if app.getGlobalContainerStack() is not self.stack or app.getBackendManager() is not self.manager:
            raise SlicerError("mode_not_applied", "Во время переключения изменилась активная машина или менеджер backend")
        expected = self.manager.getBackendByType(self.mode)
        if expected is not self.expected:
            raise SlicerError("backend_unavailable", "Во время переключения изменилась регистрация backend")
        return {"before": self.before,
                "after": _text(self.stack.getProperty("printing_mode", "value")),
                "backend": _backend_data(self.manager.getCurrentBackend(), expected)}
