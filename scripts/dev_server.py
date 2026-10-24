"""Run the chat UI without STE Slicer or Qt (dev mode: slicer tools report errors).

Usage: python scripts/dev_server.py [port]
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin"))

from AIAssistant.agent.service import AgentService
from AIAssistant.server import create_server


def main():
    service = AgentService()
    server = create_server(service, int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    print("Dev UI: http://127.0.0.1:%d/" % server.server_port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
        service.file_store.wait_closed(20)  # Standalone process only, not the Qt lifecycle.
        server.server_close()


if __name__ == "__main__":
    main()
