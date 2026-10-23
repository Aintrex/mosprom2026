import copy

from ..slicer.adapter import SlicerAdapter, validate_setting_arguments


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

    def list_tools(self):
        return copy.deepcopy(self._tools)

    def call(self, name, arguments):
        if name == "assistant_status":
            if not isinstance(arguments, dict) or arguments:
                raise ValueError("assistant_status takes no arguments")
            return {"mode": "scaffold", "llm_connected": False,
                    "slicer_tools_implemented": ["get_setting", "set_setting"],
                    "slicer_settings_scope": {"key": "layer_height", "scope": "global"},
                    "runtime_verified": False}
        if name not in ("get_setting", "set_setting"):
            raise UnknownToolError(name)
        validate_setting_arguments(arguments, write=(name == "set_setting"))
        if name == "get_setting":
            return self._adapter.get_setting(**arguments)
        return self._adapter.set_setting(**arguments)
