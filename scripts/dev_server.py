# -*- coding: utf-8 -*-
import os
import sys
import time
import webbrowser

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_PARENT = os.path.join(ROOT_DIR, "plugin")
if PLUGIN_PARENT not in sys.path:
    sys.path.insert(0, PLUGIN_PARENT)

from AIAssistant.AIAssistant import AIAssistant


def main():
    port_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    assistant = AIAssistant()
    port = assistant.startServer(host="127.0.0.1", port=port_arg)
    url = "http://127.0.0.1:%d/" % port
    print("AI Assistant запущен: %s" % url)
    print("MCP JSON-RPC endpoint: %smcp" % url)
    print("Нажмите Ctrl+C для остановки.")
    if "--no-browser" not in sys.argv:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nСервер остановлен.")


if __name__ == "__main__":
    main()