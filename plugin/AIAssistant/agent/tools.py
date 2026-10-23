import copy

from ..slicer.adapter import SlicerAdapter, validate_setting_arguments
from ..slicer.modes import PRINT_MODES, validate_mode_arguments
from ..slicer.profiles import TOOL_FIELDS, validate_profile_arguments
from ..slicer.models import MODEL_FIELDS, validate_model_arguments
from ..slicer.slicing import SLICE_FIELDS, validate_slice_arguments
from ..slicer.exports import EXPORT_FIELDS, validate_export_arguments
from ..slicer.settings import SETTING_KEYS


class UnknownToolError(Exception):
    pass


class ToolRegistry(object):
    """Single source of tool schemas for MCP and the future LLM client."""

    def __init__(self, adapter=None):
        self._adapter = adapter if adapter is not None else SlicerAdapter()
        self._tools = [{
            "name": "assistant_status",
            "description": "Read implementation status; does not inspect the slicer.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}
        }]
        for name, description in (("get_setting", "Прочитать allowlist-настройку глобального или явно указанного экструдерного стека."),
                                  ("set_setting", "Изменить одну allowlist-настройку целевого userChanges и подтвердить чтением.")):
            properties = {"key": {"type": "string", "enum": list(SETTING_KEYS)},
                          "scope": {"type": "string", "enum": ["global", "extruder"]},
                          "extruder_id": {"type": "string", "minLength": 1}}
            required = ["key", "scope"]
            if name == "set_setting":
                properties["value"] = {"type": ["number", "boolean", "string"]}
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

        for name, description in (("start_slicing", "Запустить штатную classic-нарезку; ответ не означает завершения."),
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

        self._tools.append({"name": "describe_settings", "description": "Allowlist текущего стека: реальные значения, типы/options, единицы и ограничения записи.",
                            "inputSchema": {"type": "object", "properties": {
                                "scope": {"type": "string", "enum": ["global", "extruder"]},
                                "extruder_id": {"type": "string", "minLength": 1}},
                                "required": ["scope"], "additionalProperties": False}})

    def list_tools(self):
        return copy.deepcopy(self._tools)

    def call(self, name, arguments):
        if name == "assistant_status":
            if not isinstance(arguments, dict) or arguments:
                raise ValueError("assistant_status takes no arguments")
            return {"mode": "scaffold", "llm_connected": False,
                    "slicer_tools_implemented": [tool["name"] for tool in self._tools if tool["name"] != "assistant_status"],
                    "slicer_settings_scope": {"keys": list(SETTING_KEYS), "scopes": ["global", "extruder"]},
                    "runtime_verified": False}
        if name in TOOL_FIELDS:
            validate_profile_arguments(name, arguments)
            return getattr(self._adapter, name)(**arguments)
        if name in MODEL_FIELDS:
            validate_model_arguments(name, arguments)
            return getattr(self._adapter, name)(**arguments)
        if name in SLICE_FIELDS:
            validate_slice_arguments(name, arguments)
            return getattr(self._adapter, name)(**arguments)
        if name in EXPORT_FIELDS:
            validate_export_arguments(name, arguments)
            return getattr(self._adapter, name)(**arguments)
        if name == "get_slicer_state":
            if not isinstance(arguments, dict) or arguments:
                raise ValueError("get_slicer_state takes no arguments")
            return self._adapter.get_slicer_state()
        if name == "set_print_mode":
            validate_mode_arguments(arguments)
            return self._adapter.set_print_mode(**arguments)
        if name == "describe_settings":
            validate_setting_arguments(arguments, describe=True)
            return self._adapter.describe_settings(**arguments)
        if name not in ("get_setting", "set_setting"):
            raise UnknownToolError(name)
        validate_setting_arguments(arguments, write=(name == "set_setting"))
        if name == "get_setting":
            return self._adapter.get_setting(**arguments)
        return self._adapter.set_setting(**arguments)
