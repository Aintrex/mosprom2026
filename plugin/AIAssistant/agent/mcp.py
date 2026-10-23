import json


def error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def dispatch(request, tools):
    """Minimal JSON-RPC scaffold, not a complete MCP transport implementation."""
    if not isinstance(request, dict):
        return error(None, -32600, "Invalid Request")
    request_id = request.get("id")
    method = request.get("method")
    if request.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return error(request_id, -32600, "Invalid Request")
    params = request.get("params", {})
    if not isinstance(params, dict):
        return error(request_id, -32602, "Invalid params")
    if "id" not in request:
        return None
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "steslicer-ai-scaffold", "version": "0.1.0"}
        }
    elif method == "tools/list":
        result = {"tools": tools.list_tools()}
    elif method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return error(request_id, -32602, "Invalid tool name or arguments")
        try:
            value = tools.call(name, arguments)
            result = {"content": [{"type": "text", "text": json.dumps(value)}],
                      "isError": False}
        except KeyError:
            return error(request_id, -32602, "Unknown tool")
        except ValueError as exc:
            return error(request_id, -32602, str(exc))
    else:
        return error(request_id, -32601, "Method not found")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}
