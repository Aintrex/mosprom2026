import threading
import webbrowser

from UM.Extension import Extension

from .agent.service import AgentService
from .server import create_server
from .slicer.adapter import SlicerAdapter


class AIAssistant(Extension):
    def __init__(self, app):
        super().__init__()
        self._app = app
        self._server = None
        self._adapter = SlicerAdapter(app)
        self._service = AgentService(self._adapter)
        self._closed = False
        self.setMenuName("AI Assistant")
        self.addMenuItem("Open chat", self.showChat)
        app.applicationShuttingDown.connect(self.close)

    def showChat(self):
        if self._closed:
            return
        if self._server is None:
            self._server = create_server(self._service)
            thread = threading.Thread(target=self._server.serve_forever)
            thread.daemon = True
            thread.start()
        webbrowser.open("http://127.0.0.1:%d/" % self._server.server_port)

    def close(self, *args):
        self._closed = True
        self._adapter.close()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
