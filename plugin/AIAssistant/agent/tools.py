# -*- coding: utf-8 -*-
import json

ALLOWED_MODES = ("classic", "cylindrical", "cylindrical_full")
ALLOWED_SCOPES = ("global", "extruder")


class ToolValidationError(Exception):
    pass


class ToolRegistry(object):
    def __init__(self, adapter):
        self._adapter = adapter
        self._tools = self._build_definitions()

    def _build_definitions(self):
        return [
            {
                "name": "get_printer_state",
                "description": "Получить текущее состояние принтера, активный режим печати (classic/cylindrical/cylindrical_full), профиль качества, материал и список моделей на сцене.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False
                }
            },
            {
                "name": "get_setting",
                "description": "Прочитать значение настройки слайсера (например, layer_height, wall_line_count, infill_sparse_density, support_enable).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "enum": ["global", "extruder"],
                            "description": "Контейнер настройки: global или extruder."
                        },
                        "key": {
                            "type": "string",
                            "description": "Ключ настройки в STE Slicer (например, layer_height)."
                        },
                        "extruder_id": {
                            "type": "string",
                            "description": "Позиция экструдера строкой (по умолчанию '0'), если scope='extruder'."
                        }
                    },
                    "required": ["scope", "key"],
                    "additionalProperties": False
                }
            },
            {
                "name": "set_setting",
                "description": "Изменить значение настройки слайсера (например, layer_height, wall_line_count, infill_sparse_density, support_enable, speed_print).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "enum": ["global", "extruder"],
                            "description": "Контейнер настройки: global или extruder."
                        },
                        "key": {
                            "type": "string",
                            "description": "Ключ настройки в STE Slicer (например, layer_height)."
                        },
                        "value": {
                            "description": "Новое значение настройки (число, булево значение или строка)."
                        },
                        "extruder_id": {
                            "type": "string",
                            "description": "Позиция экструдера строкой (по умолчанию '0'), если scope='extruder'."
                        }
                    },
                    "required": ["scope", "key", "value"],
                    "additionalProperties": False
                }
            },
            {
                "name": "set_print_mode",
                "description": "Переключить режим печати STE Slicer: classic (3D), cylindrical (5D Spiral) или cylindrical_full (5D).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["classic", "cylindrical", "cylindrical_full"],
                            "description": "Режим печати."
                        }
                    },
                    "required": ["mode"],
                    "additionalProperties": False
                }
            },
            {
                "name": "list_quality_profiles",
                "description": "Получить список доступных профилей качества для текущего режима и материала.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False
                }
            },
            {
                "name": "select_profile",
                "description": "Выбрать профиль качества по его quality_type.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "quality_type": {
                            "type": "string",
                            "description": "Идентификатор профиля качества из list_quality_profiles."
                        }
                    },
                    "required": ["quality_type"],
                    "additionalProperties": False
                }
            },
            {
                "name": "list_materials",
                "description": "Получить список доступных материалов для экструдера.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "extruder_id": {
                            "type": "string",
                            "description": "Строковая позиция экструдера (по умолчанию '0')."
                        }
                    },
                    "additionalProperties": False
                }
            },
            {
                "name": "select_material",
                "description": "Выбрать материал экструдера по его root_material_id из списка list_materials.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "extruder_id": {
                            "type": "string",
                            "description": "Строковая позиция экструдера (например, '0')."
                        },
                        "root_material_id": {
                            "type": "string",
                            "description": "Идентификатор материала из list_materials."
                        }
                    },
                    "required": ["extruder_id", "root_material_id"],
                    "additionalProperties": False
                }
            },
            {
                "name": "list_models",
                "description": "Получить список 3D-моделей на сцене и их габариты.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False
                }
            },
            {
                "name": "arrange_models",
                "description": "Автоматически расставить модели на рабочем столе принтера.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False
                }
            },
            {
                "name": "load_model",
                "description": "Загрузить локальный файл модели (STL/OBJ/3MF) в сцену (диагностический MCP-инструмент).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Путь к локальному файлу модели."
                        }
                    },
                    "required": ["path"],
                    "additionalProperties": False
                }
            }
        ]

    def list_tools(self):
        return list(self._tools)

    def has_tool(self, name):
        for t in self._tools:
            if t["name"] == name:
                return True
        return False

    def call(self, name, arguments):
        if not self.has_tool(name):
            raise ToolValidationError("Неизвестный инструмент: %s" % name)
        if not isinstance(arguments, dict):
            raise ToolValidationError("Аргументы инструмента должны быть JSON-объектом.")

        try:
            if name == "get_printer_state":
                return {"ok": True, "data": self._adapter.get_printer_state()}

            if name == "get_setting":
                scope = arguments.get("scope")
                key = arguments.get("key")
                if scope not in ALLOWED_SCOPES:
                    raise ToolValidationError("Параметр scope должен быть 'global' или 'extruder'.")
                if not isinstance(key, str) or not key.strip():
                    raise ToolValidationError("Параметр key должен быть непустой строкой.")
                extruder_id = str(arguments.get("extruder_id", "0"))
                return {"ok": True, "data": self._adapter.get_setting(scope, key.strip(), extruder_id=extruder_id)}

            if name == "set_setting":
                scope = arguments.get("scope")
                key = arguments.get("key")
                if scope not in ALLOWED_SCOPES:
                    raise ToolValidationError("Параметр scope должен быть 'global' или 'extruder'.")
                if not isinstance(key, str) or not key.strip():
                    raise ToolValidationError("Параметр key должен быть непустой строкой.")
                if "value" not in arguments:
                    raise ToolValidationError("Отсутствует обязательный параметр value.")
                extruder_id = str(arguments.get("extruder_id", "0"))
                return {
                    "ok": True,
                    "data": self._adapter.set_setting(scope, key.strip(), arguments["value"], extruder_id=extruder_id)
                }

            if name == "set_print_mode":
                mode = arguments.get("mode")
                if mode not in ALLOWED_MODES:
                    raise ToolValidationError("Недопустимый режим печати: %s" % mode)
                return {"ok": True, "data": self._adapter.set_print_mode(mode)}

            if name == "list_quality_profiles":
                return {"ok": True, "data": self._adapter.list_quality_profiles()}

            if name == "select_profile":
                qt = arguments.get("quality_type")
                if not isinstance(qt, str) or not qt.strip():
                    raise ToolValidationError("Параметр quality_type должен быть непустой строкой.")
                return {"ok": True, "data": self._adapter.select_profile(qt.strip())}

            if name == "list_materials":
                ext_id = str(arguments.get("extruder_id", "0"))
                return {"ok": True, "data": self._adapter.list_materials(extruder_id=ext_id)}

            if name == "select_material":
                ext_id = arguments.get("extruder_id")
                mat_id = arguments.get("root_material_id")
                if not isinstance(ext_id, str) or not ext_id.strip():
                    raise ToolValidationError("Параметр extruder_id должен быть строкой позиции экструдера.")
                if not isinstance(mat_id, str) or not mat_id.strip():
                    raise ToolValidationError("Параметр root_material_id должен быть непустой строкой.")
                return {"ok": True, "data": self._adapter.select_material(ext_id.strip(), mat_id.strip())}

            if name == "list_models":
                return {"ok": True, "data": self._adapter.list_models()}

            if name == "arrange_models":
                return {"ok": True, "data": self._adapter.arrange_models()}

            if name == "load_model":
                path = arguments.get("path")
                if not isinstance(path, str) or not path.strip():
                    raise ToolValidationError("Параметр path должен быть непустой строкой.")
                return {"ok": True, "data": self._adapter.load_model(path.strip())}

            raise ToolValidationError("Инструмент не поддерживается: %s" % name)
        except ToolValidationError:
            raise
        except Exception as e:
            return {"ok": False, "error": {"code": "execution_error", "message": str(e)}}