"""Run the scaffold without importing STE Slicer or Qt."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin"))

from AIAssistant.agent.service import AgentService
from AIAssistant.server import create_server


def main():
    server = create_server(AgentService())
    print("Scaffold: http://127.0.0.1:%d/" % server.server_port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
