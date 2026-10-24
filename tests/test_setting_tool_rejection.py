"""Pre-admission diagnostics/correction vs uncertain native execution, no real LLM/STE."""
import json
import unittest
from unittest import mock

import test_agent_integration as integration
from AIAssistant.agent.llm import LLMError
from AIAssistant.agent.service import MAX_LLM_STEPS


class SettingToolRejectionTests(unittest.TestCase):
    worker, finish = integration.AgentTests.worker, integration.AgentTests.finish
    pump_until_done, drain = integration.AgentTests.pump_until_done, integration.AgentTests.drain
    service, send, snapshot, results = integration.AgentTests.service, integration.AgentTests.send, integration.AgentTests.snapshot, integration.AgentTests.results

    def setUp(self):
        integration.AgentTests.setUp(self)

    def correction(self, arguments=None, raw=None):
        invalid = integration.tool("set_setting", arguments)
        if raw is not None:
            invalid["function"]["arguments"] = raw
        correct = {"key": "layer_height", "scope": "global", "value": 0.12}

        def corrected(client, messages):
            self.assertEqual(self.app.stack.writes, [])
            failure = json.loads([m for m in messages if m["role"] == "tool"][-1]["content"])
            error = failure["error"]
            self.assertEqual(error["code"], "invalid_tool_arguments")
            self.assertEqual(error["stage"], "validation")
            self.assertIs(error["executed"], False)
            self.assertEqual(error["expected"]["required"], ["key", "scope", "value"])
            self.assertIn("arguments_summary", error)
            return integration.calls(integration.tool("set_setting", correct, "corrected"))

        self.service(integration.calls(invalid), corrected,
                     integration.calls(integration.tool("get_setting", {"key": "layer_height", "scope": "global"}, "readback")),
                     {"content": "Чтение подтвердило изменение"})
        with mock.patch.object(self.adapter, "set_setting", wraps=self.adapter.set_setting) as native:
            self.pump_until_done(self.worker(self.send))
            self.assertEqual(native.call_count, 1)
        self.assertEqual(self.app.stack.writes, [("layer_height", 0.12)])
        self.assertEqual(self.results()[-1]["data"]["value"], 0.12)
        events = self.snapshot()["tool_events"]
        self.assertFalse(any(e["type"] == "tool_started" and e["data"]["tool_call_id"] == "call-1" for e in events))
        self.assertTrue(any(e["type"] == "tool_started" and e["data"]["tool_call_id"] == "corrected" for e in events))
        return self.results()[0]["error"]

    def test_numeric_string_rejects_before_api_corrected_once_readback(self):
        error = self.correction({"key": "layer_height", "scope": "global", "value": "0.12"})
        self.assertEqual(error["arguments_summary"]["field_types"]["value"], "string")

    def test_global_extra_null_rejects_before_api_corrected_once_readback(self):
        error = self.correction({"key": "layer_height", "scope": "global", "value": 0.12, "extruder_id": None})
        self.assertEqual(error["arguments_summary"]["field_types"]["extruder_id"], "null")
        self.assertIn("key, scope, value", error["message"])

    def test_missing_scope_rejects_before_api_no_default_corrected_once(self):
        error = self.correction({"key": "layer_height", "value": 0.12})
        self.assertEqual(error["arguments_summary"]["missing_fields"], ["scope"])

    def test_non_json_rejects_before_api_corrected_once_readback(self):
        error = self.correction(raw="{invalid private-path-and-value")
        self.assertEqual(error["arguments_summary"]["format"], "invalid_json_object")
        self.assertNotIn("private-path-and-value", json.dumps(error))

    def test_rejected_diagnostics_do_not_echo_values_unknown_keys_paths_or_secret(self):
        marker = "private-test-marker-C:/private/image.png"
        self.service(integration.calls(integration.tool("set_setting", {
            "key": "layer_height", "scope": "global", "value": marker, marker: integration.CONFIG["api_key"]})),
            {"content": "Отклонено"})
        with mock.patch.object(self.adapter, "set_setting", side_effect=AssertionError("No API")):
            self.send()
        error = self.results()[0]["error"]
        self.assertEqual(error["arguments_summary"]["unknown_fields_count"], 1)
        public = json.dumps(self.snapshot())
        self.assertNotIn(marker, public)
        self.assertNotIn(integration.CONFIG["api_key"], public)

    def test_post_admission_valueerror_is_uncertain_execution_never_safe_retry(self):
        self.service(integration.calls(integration.tool("set_setting", {"key": "layer_height", "scope": "global", "value": 0.12}),
            integration.tool("set_setting", {"key": "layer_height", "scope": "global", "value": 0.12}, "duplicate")))
        def changed_then_failed(**kwargs):
            self.app.stack.writes.append((kwargs["key"], kwargs["value"]))
            raise ValueError("private post-native error")
        with mock.patch.object(self.adapter, "set_setting", side_effect=changed_then_failed) as native:
            self.send()
            self.assertEqual(native.call_count, 1)
        self.assertEqual(self.app.stack.writes, [("layer_height", 0.12)])
        self.assertEqual([r["error"]["code"] for r in self.results()], ["execution_error", "tool_not_executed"])
        self.assertNotIn("executed", self.results()[0]["error"])
        self.assertEqual(len(self.llm.requests), 1)
        self.assertNotIn("private post-native", json.dumps(self.snapshot()))

    def test_validation_corrections_still_use_existing_step_limit(self):
        self.service(*[integration.calls(integration.tool("set_setting", {"key": "layer_height", "value": 0.12}, "bad-" + str(i)))
                       for i in range(MAX_LLM_STEPS)])
        self.send()
        self.assertEqual(len(self.llm.requests), MAX_LLM_STEPS)
        self.assertEqual(self.app.stack.writes, [])
        self.assertTrue(self.snapshot()["messages"][-1]["is_error"])

    def test_network_timeout_after_write_does_not_retry_and_readback_still_available(self):
        self.service(integration.calls(integration.tool("set_setting", {"key": "layer_height", "scope": "global", "value": 0.12})),
            LLMError("network_timeout", "Сетевой бюджет истёк"))
        self.pump_until_done(self.worker(self.send))
        self.assertEqual(self.app.stack.writes, [("layer_height", 0.12)])
        self.assertEqual(len(self.llm.requests), 2)
        self.assertTrue(self.snapshot()["messages"][-1]["is_error"])
        self.assertEqual(self.adapter.get_setting("layer_height", "global")["data"]["value"], 0.12)


if __name__ == "__main__":
    unittest.main()
