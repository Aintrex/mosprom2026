# -*- coding: utf-8 -*-
"""
Главный модуль плагина AIAssistant для STE Slicer (Cura 3.6, API v5, Python 3.5+).
Поднимает многопоточный локальный HTTP-сервер с поддержкой:
- SPA веб-интерфейса (web/index.html, app.js, style.css)
- MCP JSON-RPC 2.0 сервера (/mcp: initialize, tools/list, tools/call)
- REST API для чата, загрузки 3D-моделей/фото, настроек LLM (без хардкода ключа в исходниках!)
  и экспорта готовых проектов .3mf и .gcode.
Может запускаться как внутри STE Slicer на Windows, так и напрямую на macOS:
`python3 AIAssistant.py`
"""

import base64
import json
import os
import re
import threading
import urllib.parse
import webbrowser
import xml.etree.ElementTree as ET
import zipfile
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn

try:
    from UM.Extension import Extension
    from steslicer.SteSlicerApplication import SteSlicerApplication
    HAS_UM = True
except Exception:
    HAS_UM = False

    class Extension(object):
        def __init__(self):
            self._menu_name = ""
            self._menu_items = []

        def setMenuName(self, name):
            self._menu_name = name

        def addMenuItem(self, name, callback):
            self._menu_items.append((name, callback))

try:
    from .agent.slicer_bridge import SlicerBridge, log_info
    from .agent.mcp_server import MCPServer
    from .agent.llm_agent import LLMAgent
    from .agent.knowledge_base import PRESET_CASES
except Exception:
    from agent.slicer_bridge import SlicerBridge, log_info
    from agent.mcp_server import MCPServer
    from agent.llm_agent import LLMAgent
    from agent.knowledge_base import PRESET_CASES


PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(PLUGIN_DIR, "web")
UPLOADS_DIR = os.path.join(PLUGIN_DIR, "uploads")
EXPORTS_DIR = os.path.join(PLUGIN_DIR, "exports")
CONFIG_FILE = os.path.expanduser("~/.steslicer_ai_assistant_config.json")


def extract_docx_text(docx_path):
    """Читает текст ТЗ из .docx файла без сторонних библиотек (через zipfile + XML)."""
    if not os.path.isfile(docx_path):
        return ""
    try:
        with zipfile.ZipFile(docx_path, "r") as zf:
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        paragraphs = []
        for elem in root.iter():
            if elem.tag.endswith("}p"):
                texts = [t.text for t in elem.iter() if t.tag.endswith("}t") and t.text]
                if texts:
                    paragraphs.append("".join(texts))
        return "\n".join(paragraphs)
    except Exception as e:
        return "Ошибка чтения DOCX: %s" % str(e)


class ConfigManager(object):
    """
    Хранит настройки подключения к LLM (Base URL, Model, API Key) в настройках приложения
    STE Slicer (Preferences) и пользовательском конфигурационном файле вне исходного кода.
    Ключ НЕ хардкодится в исходниках!
    """

    PREF_PREFIX = "ai_assistant/"

    def __init__(self):
        self._data = {
            "base_url": "https://api.timeweb.ai/v1",
            "model": "dashscope/qwen3.8-flash",
            "api_key": os.environ.get("STE_LLM_API_KEY", ""),
            "examples_dir": self._detect_examples_dir()
        }
        self.load()

    def _detect_examples_dir(self):
        candidates = [
            os.path.join(os.path.dirname(PLUGIN_DIR), "Примеры деталей"),
            os.path.join(os.path.dirname(os.path.dirname(PLUGIN_DIR)), "Примеры деталей"),
            os.path.expanduser("~/Downloads/Примеры деталей"),
            os.path.expanduser("~/Downloads/Примеры деталей")
        ]
        for c in candidates:
            if os.path.isdir(c):
                return c
        return candidates[0]

    def load(self):
        if os.path.isfile(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "rt", encoding="utf-8") as f:
                    saved = json.load(f)
                    if isinstance(saved, dict):
                        self._data.update(saved)
            except Exception:
                pass

        if HAS_UM:
            try:
                app = SteSlicerApplication.getInstance()
                if app:
                    prefs = app.getPreferences()
                    for k in ("base_url", "model", "api_key", "examples_dir"):
                        full_k = self.PREF_PREFIX + k
                        prefs.addPreference(full_k, self._data.get(k, ""))
                        val = prefs.getValue(full_k)
                        if val:
                            self._data[k] = str(val)
            except Exception:
                pass

    def save(self, new_values):
        for k in ("base_url", "model", "api_key", "examples_dir"):
            if k in new_values and new_values[k] is not None:
                self._data[k] = str(new_values[k]).strip()

        try:
            with open(CONFIG_FILE, "wt", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        if HAS_UM:
            try:
                app = SteSlicerApplication.getInstance()
                if app:
                    prefs = app.getPreferences()
                    for k in ("base_url", "model", "api_key", "examples_dir"):
                        prefs.setValue(self.PREF_PREFIX + k, self._data.get(k, ""))
            except Exception:
                pass

    def get(self):
        return dict(self._data)

    def get_public(self):
        d = dict(self._data)
        key = d.get("api_key", "")
        d["has_api_key"] = bool(key)
        d["api_key_masked"] = (key[:6] + "..." + key[-4:]) if len(key) > 10 else ("***" if key else "")
        return d


class ChatServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_request_handler(assistant_instance):
    class Handler(SimpleHTTPRequestHandler):
        def translate_path(self, path):
            parsed = urllib.parse.urlparse(path).path
            if parsed == "/" or parsed == "":
                return os.path.join(WEB_DIR, "index.html")
            rel = parsed.lstrip("/")
            return os.path.join(WEB_DIR, rel)

        def log_message(self, fmt, *args):
            pass

        def _send_json(self, data, status=200):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            route = parsed.path

            if route == "/api/state":
                state = assistant_instance.bridge.get_full_state()
                state["config"] = assistant_instance.config.get_public()
                state["preset_cases"] = PRESET_CASES
                self._send_json(state)
                return

            if route == "/api/config":
                self._send_json(assistant_instance.config.get_public())
                return

            if route == "/api/mcp/tools":
                self._send_json({"tools": assistant_instance.mcp.tools})
                return

            if route == "/api/download":
                qs = urllib.parse.parse_qs(parsed.query)
                kind = (qs.get("type") or ["3mf"])[0]
                if not os.path.isdir(EXPORTS_DIR):
                    os.makedirs(EXPORTS_DIR)
                if kind == "gcode":
                    out_file = os.path.join(EXPORTS_DIR, "result.gcode")
                    assistant_instance.bridge.export_gcode(out_file)
                    mime = "text/plain; charset=utf-8"
                    dl_name = "result.gcode"
                else:
                    out_file = os.path.join(EXPORTS_DIR, "project.3mf")
                    assistant_instance.bridge.save_project_3mf(out_file)
                    mime = "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
                    dl_name = "project.3mf"

                with open(out_file, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % dl_name)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

            return super(Handler, self).do_GET()

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            route = parsed.path
            length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                data = json.loads(raw_body) if raw_body else {}
            except Exception as e:
                self._send_json({"error": "Invalid JSON: %s" % str(e)}, status=400)
                return

            # 1. MCP JSON-RPC 2.0 endpoint
            if route == "/mcp":
                rpc_resp = assistant_instance.mcp.handle_jsonrpc(data)
                if rpc_resp is None:
                    self.send_response(204)
                    self.end_headers()
                else:
                    self._send_json(rpc_resp)
                return

            # 2. Сохранение настроек LLM
            if route == "/api/config":
                assistant_instance.config.save(data)
                self._send_json({
                    "status": "saved",
                    "config": assistant_instance.config.get_public()
                })
                return

            # 3. Сброс истории чата
            if route == "/api/reset":
                assistant_instance.agent.clear_history()
                self._send_json({"status": "cleared", "state": assistant_instance.bridge.get_full_state()})
                return

            # 4. Загрузка файла (STL/OBJ/3MF или JPG/PNG в base64)
            if route == "/api/upload":
                file_name = os.path.basename(data.get("file_name", "uploaded.stl"))
                b64_content = data.get("content_base64", "")
                if "," in b64_content:
                    b64_content = b64_content.split(",", 1)[1]
                if not os.path.isdir(UPLOADS_DIR):
                    os.makedirs(UPLOADS_DIR)
                save_path = os.path.join(UPLOADS_DIR, file_name)
                with open(save_path, "wb") as f:
                    f.write(base64.b64decode(b64_content))

                ext = os.path.splitext(file_name)[1].lower()
                if ext in (".stl", ".obj", ".3mf"):
                    load_res = assistant_instance.bridge.load_model(save_path, clear_existing=True)
                    self._send_json({
                        "status": "uploaded_model",
                        "file_path": save_path,
                        "file_name": file_name,
                        "load_result": load_res,
                        "state": assistant_instance.bridge.get_full_state()
                    })
                else:
                    self._send_json({
                        "status": "uploaded_image",
                        "file_path": save_path,
                        "file_name": file_name
                    })
                return

            # 5. Загрузка одного из 7 тестовых кейсов из папки «Примеры деталей»
            if route == "/api/load_preset":
                case_id = data.get("case_id", "")
                found = None
                for c in PRESET_CASES:
                    if c["id"] == case_id:
                        found = c
                        break
                if not found:
                    self._send_json({"error": "Кейс не найден: %s" % case_id}, status=404)
                    return

                ex_dir = assistant_instance.config.get().get("examples_dir", "")
                case_dir = os.path.join(ex_dir, found["folder"])
                stl_path = os.path.join(case_dir, found["stl"])
                jpg_path = os.path.join(case_dir, found["jpg"])
                docx_path = os.path.join(case_dir, found["docx"])

                tz_text = extract_docx_text(docx_path)
                img_data_url = None
                if os.path.isfile(jpg_path):
                    with open(jpg_path, "rb") as jf:
                        b64_img = base64.b64encode(jf.read()).decode("ascii")
                        img_data_url = "data:image/jpeg;base64," + b64_img

                load_res = {}
                if os.path.isfile(stl_path):
                    load_res = assistant_instance.bridge.load_model(stl_path, clear_existing=True)

                prompt_text = (
                    "Задача печати по ТЗ (%s):\n\n%s\n\n"
                    "Проанализируй загруженную 3D-модель (%s) и фото детали, выбери режим печати (3D classic / 5D Spiral cylindrical / 5D cylindrical_full), "
                    "материал, профиль качества, настрой ключевые параметры печати в STE Slicer и запусти слайсинг."
                    % (found["title"], tz_text or found["summary"], found["stl"])
                )

                self._send_json({
                    "status": "loaded_preset",
                    "case": found,
                    "stl_path": stl_path,
                    "prompt_text": prompt_text,
                    "image_data_url": img_data_url,
                    "load_result": load_res,
                    "state": assistant_instance.bridge.get_full_state()
                })
                return

            # 6. Прямой вызов сообщения в чат ИИ-ассистента
            if route == "/api/message":
                user_text = data.get("text", "")
                images = data.get("images") or []
                model_path = data.get("model_path")
                res = assistant_instance.agent.process_user_message(
                    user_text=user_text,
                    image_data_urls=images,
                    model_path=model_path
                )
                self._send_json(res)
                return

            # 7. Сохранение проекта 3MF + G-code в папку results/<model_name>
            if route == "/api/export":
                model_folder_name = data.get("model_name") or "current_model"
                safe_name = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_\-\. ]", "_", model_folder_name).strip()
                out_dir = os.path.join(os.path.dirname(PLUGIN_DIR), "results", safe_name)
                if not os.path.isdir(out_dir):
                    os.makedirs(out_dir)
                p_3mf = os.path.join(out_dir, "project.3mf")
                p_gcode = os.path.join(out_dir, "result.gcode")
                r1 = assistant_instance.bridge.save_project_3mf(p_3mf)
                r2 = assistant_instance.bridge.export_gcode(p_gcode)
                self._send_json({
                    "status": "exported",
                    "output_dir": out_dir,
                    "project_3mf": r1,
                    "result_gcode": r2
                })
                return

            self._send_json({"error": "Route not found: %s" % route}, status=404)

    return Handler


class AIAssistant(Extension):
    def __init__(self):
        super(AIAssistant, self).__init__()
        self.setMenuName("AI Assistant")
        self.addMenuItem("Open chat", self.showChat)
        self.config = ConfigManager()
        self.bridge = SlicerBridge()
        self.mcp = MCPServer(self.bridge)
        self.agent = LLMAgent(self.mcp, self.config.get)
        self._server = None
        self._port = None

    def startServer(self, host="127.0.0.1", port=0):
        if self._server is None:
            handler_cls = make_request_handler(self)
            self._server = ChatServer((host, port), handler_cls)
            self._port = self._server.server_port
            t = threading.Thread(target=self._server.serve_forever, daemon=True)
            t.start()
            log_info("AI Assistant HTTP & MCP server started at http://%s:%d/" % (host, self._port))
        return self._port

    def showChat(self):
        port = self.startServer()
        webbrowser.open("http://127.0.0.1:%d/" % port)


if __name__ == "__main__":
    import sys
    port_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    ext = AIAssistant()
    actual_port = ext.startServer(port=port_arg)
    url = "http://127.0.0.1:%d/" % actual_port
    print("============================================================")
    print(" STE Slicer AI Assistant (Backend + MCP Server + Web SPA)")
    print(" Режим: %s" % ext.bridge.get_full_state()["runtime_mode"])
    print(" Откройте в браузере: %s" % url)
    print(" MCP JSON-RPC endpoint: %smcp" % url)
    print(" Нажмите Ctrl+C для остановки сервера.")
    print("============================================================")
    if "--no-browser" not in sys.argv:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
