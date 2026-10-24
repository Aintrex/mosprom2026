import copy
import math
import re
import time

from ..slicer.adapter import SlicerAdapter, validate_setting_arguments
from ..slicer.modes import PRINT_MODES, validate_mode_arguments
from ..slicer.profiles import TOOL_FIELDS, validate_profile_arguments
from ..slicer.models import MODEL_FIELDS, validate_model_arguments
from ..slicer.slicing import SLICE_FIELDS, validate_slice_arguments
from ..slicer.exports import EXPORT_FIELDS, validate_export_arguments
from ..slicer.settings import SETTING_KEYS, CATEGORIES
from ..slicer.settings_catalog import SETTING_TYPES
from ..slicer.transforms import TRANSFORM_FIELDS, validate_transform_arguments
from ..slicer.arrangement import ARRANGEMENT_FIELDS, validate_arrangement_arguments
from ..uploads import UploadError
from ..slicer.custom_profiles import CUSTOM_FIELDS, validate_custom_arguments


class UnknownToolError(Exception):
    pass


# Read-only waits: poll the native status from the agent worker (never on Qt).
WAIT_TOOLS = {"wait_for_slicing": ("slice_id", "get_slicing_status"),
              "wait_for_export": ("export_id", "get_export_status")}
MAX_WAIT_SECONDS, WAIT_POLL_SECONDS = 120, 1.0


# Plain decimal only: no units, expressions, hex, inf/nan or thousands separators.
DECIMAL = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?$")


def _decimal(value, integer=False):
    """Return a finite number for a strict decimal string, else None."""
    if not isinstance(value, str) or not DECIMAL.match(value.strip()):
        return None
    number = float(value.strip())
    if not math.isfinite(number):
        return None
    if integer:
        return int(number) if number == int(number) else None
    return number


class ToolRegistry(object):
    """Single source of tool schemas for MCP and the future LLM client."""

    def __init__(self, adapter=None, file_store=None):
        self._adapter = adapter if adapter is not None else SlicerAdapter()
        self._file_store = file_store
        self._tools = [{
            "name": "assistant_status",
            "description": "Read implementation status; does not inspect the slicer.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}
        }]
        for name, description in (("get_setting", "Прочитать allowlist-настройку глобального или явно указанного экструдерного стека."),
                                  ("set_setting", "Изменить одну allowlist-настройку userChanges и подтвердить чтением. Для global передай ровно key,scope,value; extruder_id не передавай даже null. layer_height: value — JSON-число в мм, например {\"key\":\"layer_height\",\"scope\":\"global\",\"value\":0.12}, не строка. Для extruder обязателен строковый runtime extruder_id.")):
            properties = {"key": {"type": "string", "enum": list(SETTING_KEYS)},
                          "scope": {"type": "string", "enum": ["global", "extruder"],
                                    "description": "Обязательно явно. global: без extruder_id; extruder: со строковым extruder_id."},
                          "extruder_id": {"type": "string", "minLength": 1,
                                          "description": "Только для scope=extruder; при global поле полностью отсутствует."}}
            required = ["key", "scope"]
            if name == "set_setting":
                properties["value"] = {"type": ["number", "boolean", "string"],
                    "description": "Тип зависит от key: float/int — JSON-число, bool — boolean, enum — строка из options. layer_height: число >0 в мм (0.12, не \"0.12\"). Без преобразования строк."}
                required.append("value")
            self._tools.append({"name": name, "description": description,
                                "inputSchema": {"type": "object", "properties": properties,
                                                "required": required, "additionalProperties": False}})
        self._tools.extend([{
            "name": "get_slicer_state",
            "description": "Прочитать машину, режим и фактический backend STE Slicer; не проверяет готовность движка.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}
        }, {
            "name": "set_print_mode",
            "description": "Переключить режим и подтвердить настройку и runtime-backend. Использовать на пустой сцене.",
            "inputSchema": {"type": "object", "properties": {"mode": {"type": "string", "enum": list(PRINT_MODES)}},
                            "required": ["mode"], "additionalProperties": False}
        }])
        descriptions = {"list_quality_profiles": "Список встроенных групп качества текущей машины, включая недоступные.",
                        "list_materials": "Материалы для явно указанной позиции экструдера и текущего сопла.",
                        "select_profile": "Выбрать встроенную QualityGroup, сохраняя пользовательские overrides.",
                        "select_material": "Выбрать root ID материала для позиции экструдера; после выбора перечитайте профили."}
        for name in ("list_quality_profiles", "list_materials", "select_profile", "select_material"):
            fields = TOOL_FIELDS[name]
            self._tools.append({"name": name, "description": descriptions[name], "inputSchema": {
                "type": "object", "properties": {field: {"type": "string", "minLength": 1} for field in fields},
                "required": list(fields), "additionalProperties": False}})
        for name, description in (("load_model", "Импорт локального STL/OBJ/3MF (до 50 MiB), с подтверждением завершения. Путь — диагностический ввод, не upload API."),
                                  ("list_models", "Адресуемые верхние модели текущей сцены и мировые границы, Uranium Y-up, мм."),
                                  ("get_model_bounds", "Актуальные мировые границы живого объекта по непрозрачному ID, мм.")):
            fields = MODEL_FIELDS[name]
            self._tools.append({"name": name, "description": description, "inputSchema": {
                "type": "object", "properties": {field: {"type": "string", "minLength": 1} for field in fields},
                "required": list(fields), "additionalProperties": False}})

        for name, description in (("start_slicing", "Запустить штатную classic/cylindrical/cylindrical_full-нарезку; ответ не означает завершения."),
                                  ("get_slicing_status", "Состояние, реальный прогресс и актуальность запуска по slice_id."),
                                  ("get_slicing_result", "Подтверждённые время и расход STE Slicer для актуального завершённого запуска.")):
            fields = SLICE_FIELDS[name]
            self._tools.append({"name": name, "description": description, "inputSchema": {
                "type": "object", "properties": {field: {"type": "string", "minLength": 1} for field in fields},
                "required": list(fields), "additionalProperties": False}})

        for name, description in (("export_gcode", "Запустить штатную запись G-code актуальной нарезки; читать get_export_status."),
                                  ("save_project_3mf", "Запустить сохранение workspace-проекта 3MF актуальной нарезки."),
                                  ("get_export_status", "Состояние записи и подтверждённый локальный артефакт по export_id.")):
            field = EXPORT_FIELDS[name]
            self._tools.append({"name": name, "description": description, "inputSchema": {
                "type": "object", "properties": {field: {"type": "string", "minLength": 1}},
                "required": [field], "additionalProperties": False}})

        self._tools.append({"name": "describe_settings", "description": "Адресные метаданные 120 разрешённых параметров: labels/описания, фактические значения/типы/options/границы. Передай keys для известных ключей или category/query для поиска; фильтры пересекаются до чтения API. По умолчанию первая страница20, max30; total/next_offset указывают остаток. query ищет по key/source-label без регистра; явная пустая строка даёт0. Не читай все страницы без запроса пользователя.",
                            "inputSchema": {"type": "object", "properties": {
                                "scope": {"type": "string", "enum": ["global", "extruder"]},
                                "extruder_id": {"type": "string", "minLength": 1},
                                "keys": {"type": "array", "minItems": 1, "maxItems": 20, "uniqueItems": True,
                                         "items": {"type": "string", "enum": list(SETTING_KEYS)}},
                                "category": {"type": "string", "enum": list(CATEGORIES)},
                                "query": {"type": "string", "maxLength": 80},
                                "offset": {"type": "integer", "minimum": 0},
                                "limit": {"type": "integer", "minimum": 1, "maximum": 30}},
                                "required": ["scope"], "additionalProperties": False}})

        descriptions = {"get_model_state": "Мировые origin/матрица 4x4/габариты и outside верхней модели, Uranium Y-up, мм.",
                        "move_model": "Штатный Undo-перенос origin верхней модели в абсолютную мировую позицию в мм. Мир Uranium: y — вертикаль (печатная Z), x/z — плоскость стола, центр стола x=0,z=0. При включённом drop down STE сам опустит модель на стол.",
                        "rotate_model": "Штатный относительный поворот верхней модели вокруг мировой оси через её origin, angle_degrees — JSON-число градусов. axis=y — вокруг вертикали (поворот в плоскости стола); x/z — положить модель на бок.",
                        "scale_model": "Штатное умножение мировых осей масштаба на факторы 0.01–100 (1 — без изменений) с сохранением origin; y — вертикаль.",
                        "delete_model": "Штатное удаление одной верхней модели/группы по ID с Undo; исходный файл не удаляется."}
        for name in ("get_model_state", "move_model", "rotate_model", "scale_model", "delete_model"):
            properties = {"object_id": {"type": "string", "minLength": 1}}
            if name in ("move_model", "scale_model"):
                value = {"type": "number", "minimum": 0.01, "maximum": 100} if name == "scale_model" else {
                    "type": "number", "minimum": -1000000, "maximum": 1000000}
                properties["factors" if name == "scale_model" else "position_mm"] = {
                    "type": "object", "properties": {a: dict(value) for a in ("x", "y", "z")},
                    "required": ["x", "y", "z"], "additionalProperties": False}
            if name == "rotate_model":
                properties.update({"axis": {"type": "string", "enum": ["x", "y", "z"]},
                                   "angle_degrees": {"type": "number", "minimum": -1000000, "maximum": 1000000}})
            self._tools.append({"name": name, "description": descriptions[name], "inputSchema": {
                "type": "object", "properties": properties, "required": list(TRANSFORM_FIELDS[name]), "additionalProperties": False}})

        for name, description in (("arrange_models", "Запустить native ArrangeObjectsJob classic для всей активной платформы; завершение/all_fit читать по ID. Только classic; требует отключённых auto-slice и push free."),
                                  ("get_arrangement_status", "Состояние своего native job, реальные after и штатные границы all_fit; не проверка всех коллизий.")):
            fields = ARRANGEMENT_FIELDS[name]
            self._tools.append({"name": name, "description": description, "inputSchema": {
                "type": "object", "properties": {field: {"type": "string", "minLength": 1} for field in fields},
                "required": list(fields), "additionalProperties": False}})

        self._tools.append({"name": "load_model_file", "description": "Импорт зарегистрированного STL/OBJ/3MF по file_id с подтверждением native-завершения; путь не принимается.",
                            "inputSchema": {"type": "object", "properties": {
                                "file_id": {"type": "string", "minLength": 1, "maxLength": 128}},
                                "required": ["file_id"], "additionalProperties": False}})

        descriptions = {"list_custom_profiles": "Пользовательские группы текущей машины со стабильными ID/доступностью/активностью.",
                        "create_custom_profile": "Штатно создать и выбрать новый пользовательский профиль из текущих настроек, подтвердить перенос и хранение.",
                        "select_custom_profile": "Выбрать доступный пользовательский профиль по ID, сохраняя user overrides.",
                        "save_custom_profile": "Сохранить текущие overrides только в активный изменяемый custom-профиль и подтвердить его файлы."}
        for name in ("list_custom_profiles", "create_custom_profile", "select_custom_profile", "save_custom_profile"):
            fields = CUSTOM_FIELDS[name]
            self._tools.append({"name": name, "description": descriptions[name], "inputSchema": {
                "type": "object", "properties": {f: {"type": "string", "minLength": 1,
                    "maxLength": 80 if f == "name" else 256} for f in fields},
                "required": list(fields), "additionalProperties": False}})

        for name, description in (("wait_for_slicing", "Подождать завершения своей нарезки по slice_id (опрос статуса раз в секунду, до max_seconds). Только чтение; возвращает последний статус. Используй после start_slicing, если пользователь просил G-code/проект."),
                                  ("wait_for_export", "Подождать завершения записи G-code/3MF по export_id (до max_seconds). Только чтение; возвращает последний статус с artifact.")):
            field = WAIT_TOOLS[name][0]
            self._tools.append({"name": name, "description": description, "inputSchema": {
                "type": "object", "properties": {field: {"type": "string", "minLength": 1},
                                                 "max_seconds": {"type": "integer", "minimum": 1, "maximum": MAX_WAIT_SECONDS}},
                "required": [field], "additionalProperties": False}})

    @staticmethod
    def coerce_llm_arguments(name, arguments):
        """LLM-boundary repair of schema-typed scalars sent as strings (e.g. "0.12").

        Only strict decimal/boolean literals for fields whose schema is numeric/boolean
        are converted; every conversion is reported back. Direct MCP stays strict.
        """
        if not isinstance(arguments, dict):
            return arguments, []
        args, notes = copy.deepcopy(arguments), []

        def fix(container, field, kind, label):
            value = container.get(field)
            if not isinstance(value, str):
                return
            if kind == "bool":
                fixed = {"true": True, "false": False}.get(value.strip().lower())
            else:
                fixed = _decimal(value, integer=(kind == "int"))
            if fixed is not None:
                container[field] = fixed
                notes.append("%s: строка преобразована в %s" % (label, "boolean" if kind == "bool" else "число"))

        if name in ("get_setting", "set_setting", "describe_settings") and args.get("scope") == "global" \
                and "extruder_id" in args and args["extruder_id"] is None:
            del args["extruder_id"]
            notes.append("extruder_id=null удалён для scope=global")
        if name == "set_setting" and SETTING_TYPES.get(args.get("key")) in ("float", "int", "bool"):
            fix(args, "value", SETTING_TYPES[args["key"]], "value")
        elif name == "rotate_model":
            fix(args, "angle_degrees", "float", "angle_degrees")
            if isinstance(args.get("axis"), str):
                args["axis"] = args["axis"].strip().lower()
        elif name in ("move_model", "scale_model"):
            field = "position_mm" if name == "move_model" else "factors"
            if isinstance(args.get(field), dict):
                for axis in ("x", "y", "z"):
                    fix(args[field], axis, "float", "%s.%s" % (field, axis))
        elif name == "describe_settings":
            for field in ("offset", "limit"):
                fix(args, field, "int", field)
        return args, notes

    def list_tools(self):
        return copy.deepcopy(self._tools)

    def argument_rejection(self, name, arguments, message, argument_format):
        # Type-only diagnostics: no raw values, unknown field names, paths or provider payload.
        schema = next((t["inputSchema"] for t in self._tools if t["name"] == name), None)
        summary = {"format": argument_format}
        if isinstance(arguments, dict) and schema:
            fields = schema.get("properties", {})
            def kind(value):
                if value is None:
                    return "null"
                if isinstance(value, bool):
                    return "boolean"
                if isinstance(value, (int, float)):
                    return "number"
                if isinstance(value, str):
                    return "string"
                return "array" if isinstance(value, list) else "object"
            summary["field_types"] = {key: kind(arguments[key]) for key in fields if key in arguments}
            summary["missing_fields"] = [key for key in schema.get("required", []) if key not in arguments]
            summary["unknown_fields_count"] = len(set(arguments) - set(fields))
        error = {"code": "invalid_tool_arguments", "message": message, "stage": "validation", "executed": False,
                 "arguments_summary": summary}
        if schema is not None:
            error["expected"] = copy.deepcopy(schema)
        return {"ok": False, "error": error}

    def validate(self, name, arguments):
        if name in ("assistant_status", "get_slicer_state"):
            if not isinstance(arguments, dict) or arguments:
                raise ValueError("%s takes no arguments" % name)
        elif name in WAIT_TOOLS:
            field = WAIT_TOOLS[name][0]
            seconds = arguments.get("max_seconds", 60) if isinstance(arguments, dict) else None
            if (not isinstance(arguments, dict) or field not in arguments or set(arguments) - {field, "max_seconds"} or
                    not isinstance(arguments[field], str) or not arguments[field] or
                    isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= MAX_WAIT_SECONDS):
                raise ValueError("Нужны %s (строка) и необязательный max_seconds 1–%d" % (field, MAX_WAIT_SECONDS))
        elif name in CUSTOM_FIELDS:
            validate_custom_arguments(name, arguments)
        elif name == "load_model_file":
            if (not isinstance(arguments, dict) or set(arguments) != {"file_id"} or
                    not isinstance(arguments["file_id"], str) or not arguments["file_id"] or len(arguments["file_id"]) > 128):
                raise ValueError("Нужен один file_id до128 символов")
        elif name in TOOL_FIELDS:
            validate_profile_arguments(name, arguments)
        elif name in MODEL_FIELDS:
            validate_model_arguments(name, arguments)
        elif name in TRANSFORM_FIELDS:
            validate_transform_arguments(name, arguments)
        elif name in ARRANGEMENT_FIELDS:
            validate_arrangement_arguments(name, arguments)
        elif name in SLICE_FIELDS:
            validate_slice_arguments(name, arguments)
        elif name in EXPORT_FIELDS:
            validate_export_arguments(name, arguments)
        elif name == "set_print_mode":
            validate_mode_arguments(arguments)
        elif name == "describe_settings":
            validate_setting_arguments(arguments, describe=True)
        elif name in ("get_setting", "set_setting"):
            validate_setting_arguments(arguments, write=(name == "set_setting"))
        else:
            raise UnknownToolError(name)

    def _wait(self, name, arguments, clock=time.monotonic, sleep=time.sleep):
        field, reader = WAIT_TOOLS[name]
        started = clock()
        deadline = started + arguments.get("max_seconds", 60)
        while True:
            result = getattr(self._adapter, reader)(arguments[field])
            data = result.get("data", {}) if isinstance(result, dict) else {}
            if not result.get("ok") or data.get("state") in ("completed", "failed") or clock() >= deadline:
                break
            sleep(min(WAIT_POLL_SECONDS, max(0, deadline - clock())))
        result = copy.deepcopy(result)
        if result.get("ok"):
            result["data"]["waited_seconds"] = round(clock() - started, 1)
            result["data"]["wait_finished"] = data.get("state") in ("completed", "failed")
        return result

    @staticmethod
    def is_mutating(name):
        return name in ("set_setting", "set_print_mode", "select_profile", "select_material", "load_model", "load_model_file",
                        "create_custom_profile", "select_custom_profile", "save_custom_profile",
                        "start_slicing", "export_gcode", "save_project_3mf", "move_model", "rotate_model", "scale_model", "delete_model", "arrange_models")

    def call(self, name, arguments):
        self.validate(name, arguments)
        if name in WAIT_TOOLS:
            return self._wait(name, arguments)
        if name == "load_model_file":
            if self._file_store is None:
                return {"ok": False, "error": {"code": "file_not_found", "message": "Хранилище вложений не подключено"}}
            try:
                lease = self._file_store.model_path(arguments["file_id"])
            except UploadError as exc:
                return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
            # Adapter owns the callback even on an early failure; tool timeout is not reader completion.
            result = self._adapter.load_model(lease.path, reader_done=lease.close)
            if result.get("ok"):
                result = copy.deepcopy(result)
                result["data"]["file_id"] = arguments["file_id"]
                result["data"]["file_name"] = lease.metadata["name"]
            return result
        if name == "assistant_status":
            return {"mode": "plugin", "llm_connected": None,
                    "slicer_tools_implemented": [tool["name"] for tool in self._tools if tool["name"] != "assistant_status"],
                    "slicer_settings_scope": {"keys": list(SETTING_KEYS), "scopes": ["global", "extruder"]},
                    "runtime_verified": False}
        return getattr(self._adapter, name)(**arguments)
