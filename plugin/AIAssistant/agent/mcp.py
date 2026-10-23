# -*- coding: utf-8 -*-
import json
from .tools import ToolValidationError


class MCPHandler(object):
    def __init__(self, registry):
        self._registry = registry

    def handle_request(self, payload):
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            return self._error(None, -32600, "Invalid Request")

        req_id = payload.get("id")
        method = payload.get("method", "")
        params = payload.get("params") or {}

        if method == "notifications/initialized":
            return None

        if method == "initialize":
            return self._result(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "steslicer-ai-assistant-mcp", "version": "1.0.0"}
            })

        if method == "ping":
            return self._result(req_id, {})

        if method == "tools/list":
            return self._result(req_id, {"tools": self._registry.list_tools()})

        if method == "tools/call":
            if not isinstance(params, dict):
                return self._error(req_id, -32602, "Invalid params")
            name = params.get("name")
            arguments = params.get("arguments")
            if arguments is None:
                arguments = {}
            try:
                outcome = self._registry.call(name, arguments)
            except ToolValidationError as e:
                return self._error(req_id, -32602, str(e))

            is_err = not bool(outcome.get("ok"))
            return self._result(req_id, {
                "content": [{"type": "text", "text": json.dumps(outcome, ensure_ascii=False)}],
                "isError": is_err
            })

        return self._error(req_id, -32601, "Method not found: %s" % method)

    def _result(self, req_id, result_obj):
        return {"jsonrpc": "2.0", "id": req_id, "result": result_obj}

    def _error(self, req_id, code, message):
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}