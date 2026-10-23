# -*- coding: utf-8 -*-
"""
Собственная реализация MCP-сервера (JSON-RPC 2.0) для Python 3.5+.
Реализует методы:
- initialize
- notifications/initialized
- tools/list
- tools/call
- ping
Также предоставляет схему инструментов в формате OpenAI Chat Completions (tools).
"""

import json
import traceback


class MCPServer(object):
    def __init__(self, slicer_bridge):
        self.bridge = slicer_bridge
        self.tools = self._build_tools_schema()

    def _build_tools_schema(self):
        return [
            {
                "name": "get_slicer_status",
                "description": "Получить полное текущее состояние STE Slicer: принтер, режим печати (classic / cylindrical / cylindrical_full), материал, профиль, настройки, модели в сцене и статус слайсинга.",
                "inputSchema": {"type": "object", "properties": {}, "required": []}
            },
            {
                "name": "list_profiles_and_materials",
                "description": "Получить список доступных режимов печати (3D/5D), профилей качества и материалов в STE Slicer.",
                "inputSchema": {"type": "object", "properties": {}, "required": []}
            },
            {
                "name": "set_printing_mode",
                "description": "Переключить режим печати принтера Stereotech Hybrid 530 V5.2.4: 'classic' (3D), 'cylindrical' (5D Spiral), или 'cylindrical_full' (5D Full).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["classic", "cylindrical", "cylindrical_full"],
                            "description": "Режим печати: classic (3D), cylindrical (5D Spiral), cylindrical_full (5D)"
                        }
                    },
                    "required": ["mode"]
                }
            },
            {
                "name": "set_profile_and_material",
                "description": "Выбрать профиль качества (draft, normal, fine, high, extra_fine) и материал печати (stereotech_proto_pla, generic_petg, stereotech_enduse_abs, generic_asa, stereotech_fiberpart_abscf, stereotech_sealant_tpu, stereotech_pa6_nylon, generic_pc, generic_pps_gf).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "quality_type": {"type": "string", "description": "ID профиля качества: draft, normal, fine, high, extra_fine"},
                        "material_id": {"type": "string", "description": "ID или название материала"}
                    },
                    "required": []
                }
            },
            {
                "name": "get_setting",
                "description": "Прочитать значение настройки слайсера (глобальной или экструдера) по ключу.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Ключ настройки"},
                        "target": {"type": "string", "enum": ["global", "extruder"]}
                    },
                    "required": ["key"]
                }
            },
            {
                "name": "set_setting",
                "description": "Изменить одну настройку слайсера (глобальную или экструдера).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Ключ настройки"},
                        "value": {"description": "Новое значение настройки"},
                        "target": {"type": "string", "enum": ["global", "extruder"]}
                    },
                    "required": ["key", "value"]
                }
            },
            {
                "name": "batch_update_settings",
                "description": "Пакетно изменить сразу несколько ключевых настроек печати (layer_height, wall_line_count, infill_sparse_density, infill_pattern, support_enable, material_print_temp, material_bed_temp, speed_print, retraction_enable, cool_fan_enabled, adhesion_type).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "layer_height": {"type": "number"},
                        "wall_line_count": {"type": "integer"},
                        "infill_sparse_density": {"type": "number"},
                        "infill_pattern": {"type": "string"},
                        "support_enable": {"type": "boolean"},
                        "material_print_temp": {"type": "number"},
                        "material_bed_temp": {"type": "number"},
                        "speed_print": {"type": "number"},
                        "retraction_enable": {"type": "boolean"},
                        "cool_fan_enabled": {"type": "boolean"},
                        "adhesion_type": {"type": "string"}
                    },
                    "required": []
                }
            },
            {
                "name": "create_custom_profile",
                "description": "Создать и сохранить пользовательский профиль качества на основе текущих настроек.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"profile_name": {"type": "string"}},
                    "required": ["profile_name"]
                }
            },
            {
                "name": "load_model",
                "description": "Загрузить 3D-модель (STL, OBJ, 3MF) в сцену слайсера и рассчитать её габариты, объём и углы нависания.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "clear_existing": {"type": "boolean"}
                    },
                    "required": ["file_path"]
                }
            },
            {
                "name": "list_scene_models",
                "description": "Получить список всех объектов в сцене с их габаритами (мм), объёмом (см3), положением, поворотом и масштабом.",
                "inputSchema": {"type": "object", "properties": {}, "required": []}
            },
            {
                "name": "analyze_model_geometry",
                "description": "Инженерный анализ геометрии 3D-модели: точные габариты X/Y/Z, объём, площадь поверхности, процент нависающих граней (>45°) и оценка осевой симметрии.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"model_id_or_path": {"type": "string"}},
                    "required": []
                }
            },
            {
                "name": "transform_model",
                "description": "Переместить, повернуть (ориентировать) или масштабировать модель на рабочем столе.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "model_id": {"type": "string"},
                        "translate": {"type": "array", "items": {"type": "number"}},
                        "rotate": {"type": "array", "items": {"type": "number"}},
                        "scale": {"type": "array", "items": {"type": "number"}}
                    },
                    "required": []
                }
            },
            {
                "name": "auto_orient_and_arrange",
                "description": "Автоматически отцентрировать и оптимально расставить все модели на рабочем столе (arrangeAll).",
                "inputSchema": {"type": "object", "properties": {}, "required": []}
            },
            {
                "name": "delete_model",
                "description": "Удалить выбранную модель или очистить сцену.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"model_id": {"type": "string"}},
                    "required": []
                }
            },
            {
                "name": "start_slicing",
                "description": "Запустить процесс слайсинга (нарезки) сцены в выбранном 3D или 5D режиме и рассчитать время печати и расход материала.",
                "inputSchema": {"type": "object", "properties": {}, "required": []}
            },
            {
                "name": "get_slicing_result",
                "description": "Получить статус и прогресс слайсинга, а также итоговое время печати и массу/длину пластика.",
                "inputSchema": {"type": "object", "properties": {}, "required": []}
            },
            {
                "name": "export_gcode",
                "description": "Экспортировать готовый G-code в указанный файл (.gcode).",
                "inputSchema": {
                    "type": "object",
                    "properties": {"output_path": {"type": "string"}},
                    "required": ["output_path"]
                }
            },
            {
                "name": "save_project_3mf",
                "description": "Сохранить настроенный проект STE Slicer в файл .3mf.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"output_path": {"type": "string"}},
                    "required": ["output_path"]
                }
            }
        ]

    def get_openai_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["inputSchema"]
                }
            }
            for t in self.tools
        ]

    def call_tool(self, name, arguments):
        if arguments is None:
            arguments = {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except Exception:
                arguments = {}

        if name == "get_slicer_status":
            return self.bridge.get_full_state()
        elif name == "list_profiles_and_materials":
            return self.bridge.list_profiles_and_materials()
        elif name == "set_printing_mode":
            return self.bridge.set_printing_mode(arguments.get("mode", "classic"))
        elif name == "set_profile_and_material":
            return self.bridge.set_profile_and_material(
                quality_type=arguments.get("quality_type"),
                material_id=arguments.get("material_id")
            )
        elif name == "get_setting":
            return self.bridge.get_setting(arguments.get("key", "layer_height"), arguments.get("target", "global"))
        elif name == "set_setting":
            return self.bridge.set_setting(arguments.get("key"), arguments.get("value"), arguments.get("target", "global"))
        elif name == "batch_update_settings":
            return self.bridge.batch_update_settings(arguments)
        elif name == "create_custom_profile":
            return self.bridge.create_custom_profile(arguments.get("profile_name", "AI Custom Profile"))
        elif name == "load_model":
            return self.bridge.load_model(arguments.get("file_path", ""), arguments.get("clear_existing", True))
        elif name == "list_scene_models":
            return self.bridge.list_scene_models()
        elif name == "analyze_model_geometry":
            return self.bridge.analyze_model_geometry(arguments.get("model_id_or_path"))
        elif name == "transform_model":
            return self.bridge.transform_model(
                model_id=arguments.get("model_id"),
                translate=arguments.get("translate"),
                rotate=arguments.get("rotate"),
                scale=arguments.get("scale")
            )
        elif name == "auto_orient_and_arrange":
            return self.bridge.auto_orient_and_arrange()
        elif name == "delete_model":
            return self.bridge.delete_model(arguments.get("model_id"))
        elif name == "start_slicing":
            return self.bridge.start_slicing()
        elif name == "get_slicing_result":
            return self.bridge.get_slicing_result()
        elif name == "export_gcode":
            return self.bridge.export_gcode(arguments.get("output_path", "result.gcode"))
        elif name == "save_project_3mf":
            return self.bridge.save_project_3mf(arguments.get("output_path", "project.3mf"))
        else:
            raise ValueError("Unknown MCP tool: %s" % name)

    def handle_jsonrpc(self, payload):
        req_id = payload.get("id")
        method = payload.get("method", "")
        params = payload.get("params") or {}

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "ste-slicer-mcp-server", "version": "1.0.0"},
                    "capabilities": {"tools": {"listChanged": False}}
                }
            elif method in ("notifications/initialized", "initialized"):
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.tools}
            elif method == "tools/call":
                tool_name = params.get("name", "")
                tool_args = params.get("arguments") or {}
                tool_output = self.call_tool(tool_name, tool_args)
                result = {
                    "content": [{"type": "text", "text": json.dumps(tool_output, ensure_ascii=False)}],
                    "structuredContent": tool_output,
                    "isError": False
                }
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": "Method not found: %s" % method}
                }

            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": str(e), "data": traceback.format_exc()}
            }
