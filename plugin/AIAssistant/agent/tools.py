import copy

from ..slicer.adapter import SlicerAdapter, validate_setting_arguments
from ..slicer.modes import PRINT_MODES, validate_mode_arguments
from ..slicer.profiles import TOOL_FIELDS, validate_profile_arguments


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
        for name, description in (("get_setting", "Прочитать реальный глобальный layer_height (мм)."),
                                  ("set_setting", "Изменить глобальный layer_height (мм) и подтвердить чтением.")):
            properties = {"key": {"type": "string", "enum": ["layer_height"]},
                          "scope": {"type": "string", "enum": ["global"]}}
            required = ["key", "scope"]
            if name == "set_setting":
                properties["value"] = {"type": "number", "exclusiveMinimum": 0}
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

    def list_tools(self):
        return copy.deepcopy(self._tools)

    def call(self, name, arguments):
        if name == "assistant_status":
            if not isinstance(arguments, dict) or arguments:
                raise ValueError("assistant_status takes no arguments")
            return {"mode": "scaffold", "llm_connected": False,
                    "slicer_tools_implemented": [tool["name"] for tool in self._tools if tool["name"] != "assistant_status"],
                    "slicer_settings_scope": {"key": "layer_height", "scope": "global"},
                    "runtime_verified": False}
        if name in TOOL_FIELDS:
            validate_profile_arguments(name, arguments)
            return getattr(self._adapter, name)(**arguments)
        if name == "get_slicer_state":
            if not isinstance(arguments, dict) or arguments:
                raise ValueError("get_slicer_state takes no arguments")
            return self._adapter.get_slicer_state()
        if name == "set_print_mode":
            validate_mode_arguments(arguments)
            return self._adapter.set_print_mode(**arguments)
        if name not in ("get_setting", "set_setting"):
            raise UnknownToolError(name)
        validate_setting_arguments(arguments, write=(name == "set_setting"))
        if name == "get_setting":
            return self._adapter.get_setting(**arguments)
        return self._adapter.set_setting(**arguments)
