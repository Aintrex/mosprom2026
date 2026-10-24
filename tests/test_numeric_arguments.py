"""Explicit LLM admission vs strict MCP, with fake provider/STE only."""
import json
import unittest
from unittest import mock

import test_agent_integration as integration
from AIAssistant.agent.llm import ChatCompletionsClient, LLMError, mcp_tools_to_openai
from AIAssistant.agent.mcp import dispatch
from AIAssistant.agent.service import json_arguments
from AIAssistant.slicer.qt_bridge import SlicerError
from AIAssistant.slicer.settings import SETTING_TYPES


class NumericArgumentsTests(unittest.TestCase):
    worker, finish = integration.AgentTests.worker, integration.AgentTests.finish
    pump_until_done, drain = integration.AgentTests.pump_until_done, integration.AgentTests.drain
    service, send = integration.AgentTests.service, integration.AgentTests.send
    snapshot, results = integration.AgentTests.snapshot, integration.AgentTests.results

    def setUp(self):
        integration.AgentTests.setUp(self)

    def arguments(self, key="layer_height", value="0.12", **extra):
        return dict({"key": key, "scope": "global", "value": value}, **extra)

    def test_wire_numeric_and_string_types_one_write_readback_and_deterministic_notice(self):
        for value in (0.12, "0.12"):
            with self.subTest(value_type=type(value).__name__):
                self.app.stack.props["layer_height"]["value"] = 0.1
                self.app.stack.writes = []
                wire = json.dumps({"choices": [{"finish_reason": "tool_calls", "message": integration.calls(
                    integration.tool("set_setting", self.arguments(value=value)))}]}).encode("utf8")
                message = ChatCompletionsClient._parse(wire)
                raw = message["tool_calls"][0]["function"]["arguments"]
                self.assertIsInstance(raw, str)
                self.assertIs(type(json_arguments(raw)["value"]), type(value))
                self.service(message, integration.calls(integration.tool("get_setting", {"key": "layer_height", "scope": "global"}, "readback")),
                             {"content": "Подтверждено отдельным чтением"})
                with mock.patch.object(self.adapter, "set_setting", wraps=self.adapter.set_setting) as native:
                    self.pump_until_done(self.worker(self.send))
                self.assertEqual(native.call_count, 1)
                self.assertIsInstance(native.call_args[1]["value"], float)
                self.assertEqual(self.app.stack.writes, [("layer_height", 0.12)])
                first, read = self.results()
                self.assertEqual((first["data"]["before"], first["data"]["after"], read["data"]["value"]), (0.1, 0.12, 0.12))
                if isinstance(value, str):
                    self.assertEqual(first["argument_admission"], {"field": "value", "from_type": "string", "to_type": "number",
                        "reason": "strict_json_number_literal", "stage": "llm_boundary"})
                    self.assertNotIn("разбор числовой строки", self.snapshot()["messages"][-1]["text"])  # Shown in the card, not the answer.
                else:
                    self.assertNotIn("argument_admission", first)
                    self.assertNotIn("разбор числовой строки", self.snapshot()["messages"][-1]["text"])

    def test_same_string_direct_mcp_and_adapter_remain_strict(self):
        with mock.patch.object(self.adapter, "set_setting", side_effect=AssertionError("Must reject before API")):
            rpc = dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "set_setting", "arguments": self.arguments()}}, self.tools)
        self.assertEqual(rpc["error"]["code"], -32602)
        with self.assertRaises(ValueError):
            self.adapter.set_setting("layer_height", "global", "0.12")
        self.assertEqual(self.app.stack.writes, [])

    def test_json_number_grammar_length_finite_underflow_and_exact_integer(self):
        for raw, value in (("0.12", 0.12), ("1.2e-1", 0.12), ("-12.5", -12.5), ("0", 0.0), ("-0e-999", -0.0),
                           ("2.2250738585072014e-308", 2.2250738585072014e-308)):
            parsed, notice = self.tools.llm_arguments("set_setting", self.arguments(value=raw))
            self.assertEqual(parsed["value"], value)
            self.assertIsInstance(parsed["value"], float)
            self.assertIsNotNone(notice)
        for raw in (" 0.12", "0.12 ", "0.12\n", "+0.12", ".12", "00.12", "0,12", "0.12mm", "0.12 mm", "1/2", "=0.12",
                    "1e309", "1e-999", "5e-324", "2.2250738585072012e-308", "NaN", "Infinity", "true", "١٢", "0" * 65):
            args = self.arguments(value=raw)
            parsed, notice = self.tools.llm_arguments("set_setting", args)
            self.assertIs(parsed, args, raw)
            self.assertIsNone(notice, raw)
        parsed, _ = self.tools.llm_arguments("set_setting", self.arguments("wall_line_count", "9007199254740993"))
        self.assertIs(type(parsed["value"]), int)
        self.assertEqual(parsed["value"], 9007199254740993)
        for raw in ("3.0", "3e0", " 3", "+3", "03", "-01"):
            args = self.arguments("wall_line_count", raw)
            self.assertIs(self.tools.llm_arguments("set_setting", args)[0], args)

    def test_no_scope_id_enum_bool_or_transform_repair_and_concrete_diagnostics(self):
        cases = [self.arguments(scope="unknown"), self.arguments(extruder_id=None), self.arguments(extra=1),
                 self.arguments("support_enable", "true"), self.arguments("infill_pattern", "grid")]
        for args in cases:
            repaired, notice = self.tools.llm_arguments("set_setting", args)
            self.assertIs(repaired, args)
            self.assertIsNone(notice)
        rotate = {"object_id": "model-1", "axis": "z", "angle_degrees": "90"}
        self.assertIs(self.tools.llm_arguments("rotate_model", rotate)[0], rotate)
        for name, args, field, expected in (("set_setting", self.arguments(value="0,12"), "value", "number"),
            ("set_setting", self.arguments("support_enable", "true"), "value", "boolean"),
            ("rotate_model", rotate, "angle_degrees", "number")):
            error = self.tools.argument_rejection(name, args, "Аргумент неверного типа", "json_object")["error"]
            self.assertIn({"field": field, "expected_type": expected, "actual_type": "string"}, error["field_errors"])
            self.assertEqual((error["stage"], error["executed"]), ("validation", False))
            if name == "set_setting":
                self.assertEqual(error["expected"]["properties"]["key"]["enum"], [args["key"]])

    def test_oneof_matches_single_catalog_and_shared_llm_mcp_schema(self):
        tools = self.tools.list_tools()
        setter = next(t for t in tools if t["name"] == "set_setting")["inputSchema"]
        types = {"float": "number", "int": "integer", "bool": "boolean", "enum": "string"}
        seen = {}
        for branch in setter["oneOf"]:
            for key in branch["properties"]["key"]["enum"]:
                self.assertNotIn(key, seen)
                seen[key] = branch["properties"]["value"]["type"]
        self.assertEqual(seen, {key: types[kind] for key, kind in SETTING_TYPES.items()})
        projected = next(t for t in mcp_tools_to_openai(tools) if t["function"]["name"] == "set_setting")
        # LLM projection drops only the top-level oneOf (provider compatibility); server validation is unchanged.
        self.assertEqual(projected["function"]["parameters"], {k: v for k, v in setter.items() if k != "oneOf"})

    def test_repeated_invalid_semantics_with_new_call_ids_stops_before_third_request(self):
        bad = self.arguments(value="0,12")
        self.service(integration.calls(integration.tool("set_setting", bad)),
            integration.calls(integration.tool("set_setting", dict(reversed(list(bad.items()))), "new-id")),
            integration.calls(integration.tool("set_setting", self.arguments(value=0.12), "never")))
        self.send()
        self.assertEqual(len(self.llm.requests), 2)
        self.assertEqual(self.app.stack.writes, [])
        self.assertTrue(self.results()[-1]["error"]["repeated"])
        self.assertTrue(self.snapshot()["messages"][-1]["is_error"])

    def test_nonfinite_raw_number_keeps_validation_error_and_no_private_echo(self):
        call = integration.tool("set_setting")
        call["function"]["arguments"] = '{"key":"layer_height","scope":"global","value":1e999}'
        self.service(integration.calls(call), {"content": "Аргументы отклонены"})
        self.send()
        error = self.results()[0]["error"]
        self.assertEqual((error["code"], error["stage"], error["executed"]), ("invalid_tool_arguments", "validation", False))
        self.assertEqual(self.app.stack.writes, [])

    def test_repaired_call_still_checks_runtime_and_stops_after_uncertain_write(self):
        self.app.stack.props["layer_height"]["enabled"] = False
        self.service(integration.calls(integration.tool("set_setting", self.arguments())), {"content": "Не должно запрашиваться"})
        self.pump_until_done(self.worker(self.send))
        self.assertEqual(self.results()[0]["error"]["code"], "setting_disabled")
        self.assertIn("argument_admission", self.results()[0])
        self.assertEqual(self.app.stack.writes, [])
        self.app.stack.props["layer_height"]["enabled"] = True
        self.app.stack.setter_failure = SlicerError("qt_timeout", "Исход fake-записи неизвестен")
        self.service(integration.calls(integration.tool("set_setting", self.arguments()),
            integration.tool("set_setting", self.arguments(value="0.13"), "never")))
        self.pump_until_done(self.worker(self.send))
        self.assertEqual(self.app.stack.writes, [("layer_height", 0.12)])
        self.assertEqual([r["error"]["code"] for r in self.results()], ["qt_timeout", "tool_not_executed"])
        self.assertEqual(len(self.llm.requests), 1)
        self.assertNotIn("разбор числовой строки", self.snapshot()["messages"][-1]["text"])  # Shown in the card, not the answer.

    def test_network_timeout_after_repaired_write_does_not_retry(self):
        self.service(integration.calls(integration.tool("set_setting", self.arguments())), LLMError("network_timeout", "Не получен ответ"))
        self.pump_until_done(self.worker(self.send))
        self.assertEqual(self.app.stack.writes, [("layer_height", 0.12)])
        self.assertEqual(len(self.llm.requests), 2)
        self.assertTrue(self.snapshot()["messages"][-1]["is_error"])


if __name__ == "__main__":
    unittest.main()
