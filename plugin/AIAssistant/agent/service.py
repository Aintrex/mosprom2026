from .tools import ToolRegistry


class AgentService(object):
    def __init__(self, adapter=None):
        self.tools = ToolRegistry(adapter)

    def message(self, text):
        # TODO: replace with a bounded Chat Completions/tool calling loop.
        return {
            "reply": "Каркас получил запрос: %s\nLLM и управление слайсером пока не подключены." % text,
            "events": [],
            "mode": "scaffold"
        }
