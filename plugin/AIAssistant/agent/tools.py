class ToolRegistry(object):
    """Single source of tool schemas for MCP and the future LLM client."""

    def list_tools(self):
        # Add real slicer tools only alongside their implementations.
        return [{
            "name": "assistant_status",
            "description": "Read scaffold status; does not inspect the slicer.",
            "inputSchema": {
                "type": "object", "properties": {},
                "additionalProperties": False
            }
        }]

    def call(self, name, arguments):
        if name != "assistant_status":
            raise KeyError(name)
        if arguments:
            raise ValueError("assistant_status takes no arguments")
        return {"mode": "scaffold", "llm_connected": False,
                "slicer_tools_implemented": False}
