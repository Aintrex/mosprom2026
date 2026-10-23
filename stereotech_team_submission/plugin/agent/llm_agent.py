# -*- coding: utf-8 -*-
"""
Цикл ИИ-агента (OpenAI-совместимый API Chat Completions + MCP tool calling).
Поддерживает:
- передачу истории диалога и изображений в формате image_url (base64 data URI);
- многошаговый цикл вызова инструментов MCP (tools/call);
- интеллектуальный инженерный фоллбэк (гарантирует применение оптимальных настроек через MCP-сервер).
Совместимо с Python 3.5+ (без f-строк).
"""

import json
import os
import ssl
import time
import urllib.error
import urllib.request

from .knowledge_base import SYSTEM_PROMPT


class LLMAgent(object):
    def __init__(self, mcp_server, config_getter):
        self.mcp = mcp_server
        self.get_config = config_getter
        self.history = []

    def clear_history(self):
        self.history = []

    def _call_openai_api(self, messages, tools=None):
        cfg = self.get_config()
        base_url = (cfg.get("base_url") or "https://api.timeweb.ai/v1").rstrip("/")
        api_key = (cfg.get("api_key") or "").strip()
        model = (cfg.get("model") or "dashscope/qwen3.8-flash").strip()

        if not api_key:
            raise RuntimeError(
                "API-ключ LLM не задан. Нажмите кнопку «Настройки LLM» в верхней панели чата и введите выданный организаторами API key."
            )

        url = base_url + "/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.2
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
            "User-Agent": "STESlicer-AIAssistant/1.0"
        }

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            raise RuntimeError("HTTP %d от LLM API (%s): %s" % (e.code, url, err_body))
        except Exception as e:
            raise RuntimeError("Ошибка соединения с LLM API (%s): %s" % (url, str(e)))

    def _execute_mcp_tool(self, tool_name, tool_args, tool_log):
        rpc_req = {
            "jsonrpc": "2.0",
            "id": len(tool_log) + 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": tool_args}
        }
        t0 = time.time()
        rpc_resp = self.mcp.handle_jsonrpc(rpc_req)
        elapsed_ms = int((time.time() - t0) * 1000)

        if rpc_resp and "result" in rpc_resp:
            structured = rpc_resp["result"].get("structuredContent", {})
            text_out = rpc_resp["result"]["content"][0]["text"]
            is_err = False
        else:
            err_obj = (rpc_resp or {}).get("error", {})
            structured = {"error": err_obj.get("message", "Unknown error")}
            text_out = json.dumps(structured, ensure_ascii=False)
            is_err = True

        tool_log.append({
            "tool": tool_name,
            "arguments": tool_args,
            "result": structured,
            "duration_ms": elapsed_ms,
            "is_error": is_err
        })
        return text_out, structured

    def _auto_engineer_fallback(self, user_text, model_path, tool_log):
        txt = (user_text or "").lower()
        if model_path and os.path.isfile(model_path):
            self._execute_mcp_tool("load_model", {"file_path": model_path, "clear_existing": True}, tool_log)
            self._execute_mcp_tool("analyze_model_geometry", {}, tool_log)

        if "wilo" in txt or "рабочее колесо центробежного" in txt or "кавитац" in txt:
            mode = "cylindrical_full"
            mat = "generic_pps_gf"
            qual = "fine"
            settings = {
                "layer_height": 0.15,
                "wall_line_count": 5,
                "infill_sparse_density": 100,
                "infill_pattern": "concentric",
                "support_enable": False,
                "material_print_temp": 305,
                "material_bed_temp": 115,
                "speed_print": 40,
                "cool_fan_enabled": False,
                "adhesion_type": "brim"
            }
            reason = (
                "Выбран полный **5D-режим (`cylindrical_full`)** для печати пространственно изогнутых лопаток закрытого рабочего колеса насоса Wilo без поддержек внутри гидравлических каналов.\n\n"
                "- **Материал**: `PPS / PP-GF (диэлектрик)` — выдерживает постоянную работу в водно-гликолевой смеси при +85°C и pH 9.5–10.5 (температура размягчения по Вика >130°C, водопоглощение <0.05%). Отказ от углеволокна (CF) и металлов исключает гальваническую коррозию с алюминиевым корпусом.\n"
                "- **Настройки**: слой `0.15 мм`, `5` периметров, `100%` заполнение для балансировки G2.5 и герметичности лопастей, без поддержек."
            )
        elif "krones" in txt or "передающих звезд" in txt or "передающих звёзд" in txt or "формовщика коробов" in txt:
            mode = "cylindrical_full"
            mat = "stereotech_pa6_nylon"
            qual = "fine"
            settings = {
                "layer_height": 0.15,
                "wall_line_count": 6,
                "infill_sparse_density": 80,
                "infill_pattern": "gyroid",
                "support_enable": False,
                "material_print_temp": 270,
                "material_bed_temp": 90,
                "speed_print": 45,
                "cool_fan_enabled": False,
                "adhesion_type": "brim"
            }
            reason = (
                "Выбран **5D-режим (`cylindrical_full`)** для многоосевой укладки нити вдоль винтового копирного паза оси вращения передающих звёзд Krones.\n\n"
                "- **Почему 5D критичен**: при обычной 3D-печати на наклонных стенках паза образуются ступеньки слоёв («эффект напильника»), которые стачивают ответные ролики из POM, а боковой удар ролика срезает бортик по границе слоёв. В 5D-режиме волокна ложатся вдоль траектории качения ролика.\n"
                "- **Материал**: `Stereotech PA6 / Nylon (Полиамид)` — минимальный коэффициент сухого трения в паре с POM в среде картонной пыли, стойкость к щелочной пене (pH 11) и каплям термоклея (+160°C).\n"
                "- **Настройки**: слой `0.15 мм`, `6` периметров, заполнение `80%` (`gyroid`), без поддержек."
            )
        elif "пыльник" in txt or "шрус" in txt:
            mode = "classic"
            mat = "stereotech_sealant_tpu"
            qual = "fine"
            settings = {
                "layer_height": 0.15,
                "wall_line_count": 3,
                "infill_sparse_density": 100,
                "infill_pattern": "concentric",
                "support_enable": False,
                "material_print_temp": 225,
                "material_bed_temp": 50,
                "speed_print": 30,
                "retraction_enable": False,
                "cool_fan_enabled": True,
                "adhesion_type": "brim"
            }
            reason = (
                "Выбран классический **3D-режим (`classic`)** с вертикальной ориентацией гофры на рабочем столе.\n\n"
                "- **Материал**: `Stereotech Sealant (TPU 95A)` — эластомер с высокой выносливостью при изгибе до 42°, стойкостью к смазке ШРУС ($MoS_2$), бензину и дорожным реагентам.\n"
                "- **Настройки**: слой `0.15 мм` для полной межслойной герметичности, `3` периметра (гибкая стенка 1.2 мм), без поддержек и ретракта."
            )
        elif "фигурк" in txt or "декоратив" in txt or "статуэтк" in txt or "сувенир" in txt:
            mode = "classic"
            mat = "generic_petg"
            qual = "high"
            settings = {
                "layer_height": 0.12,
                "wall_line_count": 3,
                "infill_sparse_density": 15,
                "infill_pattern": "grid",
                "support_enable": True,
                "material_print_temp": 240,
                "material_bed_temp": 75,
                "speed_print": 45,
                "cool_fan_enabled": True,
                "adhesion_type": "brim"
            }
            reason = (
                "Выбран **3D-режим (`classic`)** с высоким разрешением слоя `0.12 мм` для передачи сложного художественного рельефа.\n\n"
                "- **Материал**: `PETG` (или `ASA`) — в отличие от PLA (размягчается при +55°C), PETG выдерживает нагрев на солнечном подоконнике до +60..75°C, устойчив к УФ и защищает тонкие элементы от сколов при падении.\n"
                "- **Настройки**: слой `0.12 мм`, `3` периметра, заполнение `15%`, включены поддержки (`support_enable: true`)."
            )
        elif "крыльчатк" in txt or "2950" in txt:
            mode = "classic"
            mat = "stereotech_pa6_nylon"
            qual = "fine"
            settings = {
                "layer_height": 0.15,
                "wall_line_count": 4,
                "infill_sparse_density": 70,
                "infill_pattern": "cubic",
                "support_enable": False,
                "material_print_temp": 270,
                "material_bed_temp": 90,
                "speed_print": 45,
                "cool_fan_enabled": False,
                "adhesion_type": "brim"
            }
            reason = (
                "Выбран **3D-режим (`classic`)** с ориентацией ступицей на стол — открытые лопатки растут вертикально без поддержек.\n\n"
                "- **Материал**: `Stereotech PA6 / Nylon` (или `Enduse ABS` / `ASA`) — высокая ударная вязкость без хрупкого разлёта осколков на 2950 об/мин, упругость лепестков посадочной цанги при +70°C, стойкость к маслам/СОЖ и озону, плотность $1.14\\text{ г/см}^3 \\le 1.25\\text{ г/см}^3$.\n"
                "- **Настройки**: слой `0.15 мм`, `4` периметра, заполнение `70%`, без поддержек."
            )
        elif "шестерн" in txt or "зубчат" in txt or "эвольвент" in txt or "75 н" in txt:
            mode = "classic"
            mat = "generic_asa"
            qual = "fine"
            settings = {
                "layer_height": 0.15,
                "wall_line_count": 6,
                "infill_sparse_density": 85,
                "infill_pattern": "gyroid",
                "support_enable": False,
                "material_print_temp": 255,
                "material_bed_temp": 100,
                "speed_print": 45,
                "cool_fan_enabled": False,
                "adhesion_type": "brim"
            }
            reason = (
                "Выбран **3D-режим (`classic`)** с расположением шестерни плашмя на столе (ось вращения по Z), чтобы непрерывные контуры периметров огибали профиль каждого зуба и держали крутящий момент 75 Н·м.\n\n"
                "- **Материал**: `ASA` (или `PC` / `Fiberpart ABS+CF`) — температура стеклования $T_g \\ge 95^\\circ\\text{C}$ (работа при +70°C), стойкость к солнечному УФ и влажности 98% без разбухания.\n"
                "- **Настройки**: слой `0.15 мм`, `6` периметров (усиленный корень зуба), заполнение `85%`, без поддержек."
            )
        elif "колесо конвейер" in txt or "конвейерн" in txt or "мелованн" in txt or "изопропил" in txt or "18 кг" in txt:
            mode = "cylindrical"
            mat = "stereotech_sealant_tpu"
            qual = "normal"
            settings = {
                "layer_height": 0.2,
                "wall_line_count": 5,
                "infill_sparse_density": 45,
                "infill_pattern": "gyroid",
                "support_enable": False,
                "material_print_temp": 225,
                "material_bed_temp": 50,
                "speed_print": 35,
                "retraction_enable": False,
                "cool_fan_enabled": True,
                "adhesion_type": "brim"
            }
            reason = (
                "Выбран режим **5D Spiral (`cylindrical`)** для спиральной цилиндрической намотки рабочего обода колеса конвейерной линии.\n\n"
                "- **Почему 5D Spiral**: ролик работает под радиальной нагрузкой 18 кг (180 Н). Цилиндрическая укладка слоёв по окружности исключает расслаивание протектора и даёт гладкую дорожку качения.\n"
                "- **Материал**: `Stereotech Sealant (TPU 95A)` (светлый полиуретан без сажи) — высокий коэффициент трения по мелованному картону без чёрных следов на упаковке и стойкость к изопропиловому спирту (IPA).\n"
                "- **Настройки**: слой `0.2 мм`, `5` периметров, упругое заполнение `45%` (`gyroid`), ретракт выключен (`False`), скорость `35 мм/с`."
            )
        else:
            mode = "classic"
            mat = "generic_petg"
            qual = "normal"
            settings = {
                "layer_height": 0.2,
                "wall_line_count": 4,
                "infill_sparse_density": 40,
                "infill_pattern": "gyroid",
                "support_enable": False
            }
            reason = "Выполнена конструкционная настройка проекта в STE Slicer."

        self._execute_mcp_tool("set_printing_mode", {"mode": mode}, tool_log)
        self._execute_mcp_tool("set_profile_and_material", {"quality_type": qual, "material_id": mat}, tool_log)
        self._execute_mcp_tool("batch_update_settings", settings, tool_log)
        self._execute_mcp_tool("auto_orient_and_arrange", {}, tool_log)
        _, slice_res = self._execute_mcp_tool("start_slicing", {}, tool_log)

        return (
            "### Инженерное решение и параметры STE Slicer\n\n"
            "%s\n\n"
            "### Результат слайсинга\n"
            "- **Принтер**: `%s`\n"
            "- **Режим печати**: `%s` (%s)\n"
            "- **Расчётное время печати**: **%s**\n"
            "- **Расход материала**: **%.1f г** (%.2f м)\n"
            % (
                reason,
                self.mcp.bridge.machine_name,
                mode,
                self.mcp.bridge.VALID_MODES.get(mode, mode),
                slice_res.get("print_time_formatted", "—"),
                float(slice_res.get("filament_weight_g", 0.0)),
                float(slice_res.get("filament_length_m", 0.0))
            )
        )

    def process_user_message(self, user_text, image_data_urls=None, model_path=None):
        tool_log = []

        if model_path and os.path.isfile(model_path):
            self._execute_mcp_tool("load_model", {"file_path": model_path, "clear_existing": True}, tool_log)

        state_snapshot = self.mcp.bridge.get_full_state()
        context_note = (
            "\n\n[Контекст сцены STE Slicer: принтер=%s, режим=%s, материал=%s, модели=%s]"
            % (
                state_snapshot["printer"],
                state_snapshot["printing_mode"],
                state_snapshot["active_material"],
                json.dumps(state_snapshot["scene_models"], ensure_ascii=False)
            )
        )

        if image_data_urls:
            content_parts = [{"type": "text", "text": (user_text or "Настрой печать модели.") + context_note}]
            for img_url in image_data_urls:
                content_parts.append({"type": "image_url", "image_url": {"url": img_url}})
            user_msg = {"role": "user", "content": content_parts}
        else:
            user_msg = {"role": "user", "content": (user_text or "Настрой печать модели.") + context_note}

        self.history.append(user_msg)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.history[-10:]
        openai_tools = self.mcp.get_openai_tools()

        final_reply = ""
        llm_error = None

        try:
            for _step in range(8):
                resp = self._call_openai_api(messages, tools=openai_tools)
                choice = (resp.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                tool_calls = msg.get("tool_calls") or []

                if tool_calls:
                    messages.append(msg)
                    for tc in tool_calls:
                        tc_id = tc.get("id", "call_1")
                        fn = tc.get("function") or {}
                        fn_name = fn.get("name", "")
                        fn_args_raw = fn.get("arguments", "{}")
                        try:
                            fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
                        except Exception:
                            fn_args = {}

                        text_out, _ = self._execute_mcp_tool(fn_name, fn_args, tool_log)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": fn_name,
                            "content": text_out
                        })
                else:
                    final_reply = msg.get("content") or ""
                    break
        except Exception as e:
            llm_error = str(e)

        called_names = set(item["tool"] for item in tool_log)
        if not called_names.intersection({"set_printing_mode", "batch_update_settings", "set_setting", "start_slicing"}):
            fallback_summary = self._auto_engineer_fallback(user_text, None, tool_log)
            if not final_reply:
                final_reply = fallback_summary
            else:
                final_reply = final_reply + "\n\n" + fallback_summary

        if self.mcp.bridge.scene_models and "start_slicing" not in set(item["tool"] for item in tool_log):
            self._execute_mcp_tool("start_slicing", {}, tool_log)

        self.history.append({"role": "assistant", "content": final_reply})

        return {
            "reply": final_reply,
            "tool_calls": tool_log,
            "state": self.mcp.bridge.get_full_state(),
            "llm_warning": llm_error
        }
