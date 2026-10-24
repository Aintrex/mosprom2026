import copy
import decimal
import re
import sys

from ..slicer.adapter import SlicerAdapter, validate_setting_arguments
from ..slicer.modes import PRINT_MODES, validate_mode_arguments
from ..slicer.profiles import TOOL_FIELDS, validate_profile_arguments
from ..slicer.models import MODEL_FIELDS, validate_model_arguments
from ..slicer.slicing import SLICE_FIELDS, validate_slice_arguments
from ..slicer.exports import EXPORT_FIELDS, validate_export_arguments
from ..slicer.settings import SETTING_KEYS, SETTING_TYPES, CATEGORIES, finite
from ..slicer.transforms import TRANSFORM_FIELDS, validate_transform_arguments
from ..slicer.arrangement import ARRANGEMENT_FIELDS, validate_arrangement_arguments
from ..uploads import UploadError
from ..slicer.custom_profiles import CUSTOM_FIELDS, validate_custom_arguments


class UnknownToolError(Exception):
    pass


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
            if name == "set_setting":
                self._tools[-1]["inputSchema"]["oneOf"] = [
                    {"properties": {"key": {"enum": [key for key in SETTING_KEYS if SETTING_TYPES[key] == kind]},
                                    "value": {"type": json_type}}, "required": ["key", "value"]}
                    for kind, json_type in (("float", "number"), ("int", "integer"), ("bool", "boolean"), ("enum", "string"))]
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
                        "move_model": "Штатный Undo-перенос origin верхней модели в абсолютную мировую позицию в мм (Y вверх). Требует отключённой физики сцены.",
                        "rotate_model": "Штатный относительный поворот верхней модели вокруг мировой оси через её origin, градусы; без физики сцены.",
                        "scale_model": "Штатное умножение мировых осей масштаба на факторы 0.01–100 с сохранением origin; без физики сцены.",
                        "delete_model": "Штатное удаление одной верхней модели/группы по ID с Undo; исходный файл не удаляется. Без физики сцены."}
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

        for name, description in (("arrange_models", "Запустить native ArrangeObjectsJob classic для всей активной платформы; завершение/all_fit читать по ID. Требует отключённых auto-slice и физики."),
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

    def list_tools(self):
        return copy.deepcopy(self._tools)

    @staticmethod
    def llm_arguments(name, arguments):
        """Explicit LLM-boundary repair; MCP/call/Qt never use this method."""
        if name != "set_setting" or not isinstance(arguments, dict):
            return arguments, None
        key, value, scope = arguments.get("key"), arguments.get("value"), arguments.get("scope")
        kind = SETTING_TYPES.get(key) if isinstance(key, str) else None
        fields = {"key", "scope", "value"} | ({"extruder_id"} if scope == "extruder" else set())
        if (kind not in ("float", "int") or not isinstance(value, str) or not 1 <= len(value) <= 64 or
                scope not in ("global", "extruder") or set(arguments) != fields):
            return arguments, None
        pattern = r"\A-?(0|[1-9][0-9]*)\Z" if kind == "int" else r"\A-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?\Z"
        if not re.match(pattern, value):
            return arguments, None
        try:
            if kind == "int":
                parsed = int(value)  # No float intermediate or precision loss.
            else:
                exact, parsed = decimal.Decimal(value), float(value)
                if (not exact.is_finite() or not finite(parsed) or
                        not exact.is_zero() and (parsed == 0 or exact.copy_abs() < decimal.Decimal.from_float(sys.float_info.min))):
                    return arguments, None
        except (ValueError, OverflowError, decimal.InvalidOperation):
            return arguments, None
        repaired = dict(arguments)
        repaired["value"] = parsed
        return repaired, {"field": "value", "from_type": "string", "to_type": "integer" if kind == "int" else "number",
                          "reason": "strict_json_integer_literal" if kind == "int" else "strict_json_number_literal",
                          "stage": "llm_boundary"}

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
        if isinstance(arguments, dict) and schema:
            types = {key: field.get("type") for key, field in schema.get("properties", {}).items()}
            key = arguments.get("key")
            if name == "set_setting" and isinstance(key, str) and key in SETTING_TYPES:
                types["value"] = {"float": "number", "int": "integer", "bool": "boolean", "enum": "string"}[SETTING_TYPES[key]]
                # A concrete repair needs this key/type, not another copy of120 keys.
                error["expected"]["properties"]["key"]["enum"] = [key]
                error["expected"]["properties"]["value"]["type"] = types["value"]
                error["expected"].pop("oneOf", None)
            issues = []
            for field, expected in types.items():
                if field not in arguments:
                    if field in schema.get("required", []):
                        issues.append({"field": field, "expected_type": expected, "actual_type": "missing"})
                elif isinstance(expected, str):
                    actual = kind(arguments[field])
                    integer = expected == "integer" and finite(arguments[field]) and int(arguments[field]) == arguments[field]
                    if actual != expected and not integer:
                        issues.append({"field": field, "expected_type": expected, "actual_type": actual})
                    elif expected == "number" and not finite(arguments[field]):
                        issues.append({"field": field, "expected_type": expected, "actual_type": actual, "constraint": "finite"})
                    allowed = schema["properties"][field].get("enum")
                    if allowed is not None and arguments[field] not in allowed:
                        issue = {"field": field, "expected_type": expected, "actual_type": actual, "constraint": "enum"}
                        if len(allowed) <= 10:
                            issue["allowed_values"] = list(allowed)
                        issues.append(issue)
            # Nested transform vectors use the very same schema, never guessed values.
            for vector in ("position_mm", "factors"):
                if isinstance(arguments.get(vector), dict):
                    for axis in ("x", "y", "z"):
                        value = arguments[vector].get(axis)
                        if not finite(value):
                            issues.append({"field": vector + "." + axis, "expected_type": "number", "actual_type": kind(value) if axis in arguments[vector] else "missing"})
            if issues:
                error["field_errors"] = issues
        return {"ok": False, "error": error}

    def validate(self, name, arguments):
        if name in ("assistant_status", "get_slicer_state"):
            if not isinstance(arguments, dict) or arguments:
                raise ValueError("%s takes no arguments" % name)
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

    @staticmethod
    def is_mutating(name):
        return name in ("set_setting", "set_print_mode", "select_profile", "select_material", "load_model", "load_model_file",
                        "create_custom_profile", "select_custom_profile", "save_custom_profile",
                        "start_slicing", "export_gcode", "save_project_3mf", "move_model", "rotate_model", "scale_model", "delete_model", "arrange_models")

    def call(self, name, arguments):
        self.validate(name, arguments)
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
            return {"mode": "scaffold", "llm_connected": False,
                    "slicer_tools_implemented": [tool["name"] for tool in self._tools if tool["name"] != "assistant_status"],
                    "slicer_settings_scope": {"keys": list(SETTING_KEYS), "scopes": ["global", "extruder"]},
                    "runtime_verified": False}
        return getattr(self._adapter, name)(**arguments)
