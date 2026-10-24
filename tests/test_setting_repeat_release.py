"""Repeat report: actual service/client path with fake wire provider and Qt/STE."""
import copy
import io
import json
from pathlib import Path
import re
import unittest
from unittest import mock

import test_agent_integration as integration
from AIAssistant.agent.llm import ChatCompletionsClient
from AIAssistant.agent.service import AgentService, NUMERIC_ARGUMENT_NOTICE
from AIAssistant.release import public_release, NUMERIC_ARGUMENT_POLICY


class RepeatSettingPathTests(unittest.TestCase):
    worker, finish = integration.AgentTests.worker, integration.AgentTests.finish
    pump_until_done = integration.AgentTests.pump_until_done

    def setUp(self):
        integration.AgentTests.setUp(self)
        self.app.stack.props["cylindrical_layer_height"] = copy.deepcopy(self.app.stack.props["layer_height"])

    def run_provider(self, key, value, extra=None):
        arguments = dict({"key": key, "scope": "global", "value": value}, **(extra or {}))
        responses = [integration.calls(integration.tool("set_setting", arguments)),
                     integration.calls(integration.tool("get_setting", {"key": key, "scope": "global"}, "readback")),
                     {"role": "assistant", "content": "Проверено отдельным чтением"}]
        calls = []
        def provider(request, **kwargs):
            calls.append(json.loads(request.data.decode("utf8")))
            message = responses.pop(0)
            return io.BytesIO(json.dumps({"choices": [{"finish_reason": "tool_calls" if message.get("tool_calls") else "stop", "message": message}]}).encode("utf8"))
        client = ChatCompletionsClient(provider)
        config = dict(integration.CONFIG, base_url="http://provider.invalid/v1")
        agent = AgentService(self.adapter, lambda: dict(config), client, run_async=False)
        self.addCleanup(agent.close)
        sid = agent.create_session()["session_id"]
        with mock.patch.object(self.adapter, "set_setting", wraps=self.adapter.set_setting) as native:
            self.pump_until_done(self.worker(lambda: agent.submit_message(sid, {
                "text": "Измени только запрошенную высоту на0.12 и перечитай", "client_message_id": "repeat-case"})))
        snapshot = agent.get_session(sid)
        results = [event["data"]["result"] for event in snapshot["tool_events"] if event["type"] == "tool_finished"]
        return snapshot, results, calls, native

    def test_actual_client_service_path_both_keys_number_and_exact_numeric_string(self):
        for key in ("layer_height", "cylindrical_layer_height"):
            for value in (0.12, "0.12"):
                with self.subTest(key=key, input_type=type(value).__name__):
                    self.app.stack.props[key]["value"] = 0.1
                    self.app.stack.writes = []
                    snapshot, results, calls, native = self.run_provider(key, value)
                    self.assertEqual(native.call_count, 1)
                    self.assertIs(type(native.call_args[1]["value"]), float)
                    self.assertEqual(native.call_args[1]["key"], key)
                    self.assertEqual(self.app.stack.writes, [(key, 0.12)])
                    self.assertEqual((results[0]["data"]["before"], results[0]["data"]["after"], results[1]["data"]["value"]), (0.1, 0.12, 0.12))
                    self.assertEqual(len(calls), 3)
                    self.assertEqual(NUMERIC_ARGUMENT_NOTICE in snapshot["messages"][-1]["text"], isinstance(value, str))
                    diagnostic = results[0]["argument_diagnostics"]
                    self.assertEqual(diagnostic["policy"], NUMERIC_ARGUMENT_POLICY)
                    self.assertTrue(diagnostic["normalizer_present"])
                    self.assertEqual(diagnostic["reason"], "normalized" if isinstance(value, str) else "already_json_number")

    def test_global_extra_and_null_extruder_id_are_not_silently_removed(self):
        for key in ("layer_height", "cylindrical_layer_height"):
            for extra in ({"extruder_id": None}, {"unexpected": True}):
                with self.subTest(key=key, extra_field=next(iter(extra))):
                    self.app.stack.writes = []
                    snapshot, results, calls, native = self.run_provider(key, "0.12", extra)
                    self.assertEqual(native.call_count, 0)
                    self.assertEqual(self.app.stack.writes, [])
                    error = results[0]["error"]
                    self.assertEqual((error["code"], error["stage"], error["executed"]), ("invalid_tool_arguments", "validation", False))
                    self.assertEqual(error["arguments_summary"]["field_types"]["value"], "string")
                    self.assertNotIn("argument_admission", results[0])
                    diagnostic = results[0]["argument_diagnostics"]
                    self.assertEqual((diagnostic["reason"], diagnostic["unexpected_fields_count"]), ("invalid_structure", 1))
                    self.assertEqual(diagnostic["extruder_id_present"], "extruder_id" in extra)
                    if "extruder_id" in extra:
                        self.assertEqual(diagnostic["extruder_id_type"], "null")

    def test_service_skip_reasons_no_raw_values_and_no_expanded_repair(self):
        marker = "private-fixture-content"
        for value, reason in ((" 0.12", "not_json_number_literal"), ('"0.12"', "not_json_number_literal"),
                              ("0,12", "not_json_number_literal"), ("0.12 mm", "not_json_number_literal"),
                              ("1e309", "non_finite_or_overflow"), ("1e-999", "underflow"),
                              ("0" * 65, "literal_size_invalid"), (marker, "not_json_number_literal")):
            with self.subTest(reason=reason):
                _, results, _, native = self.run_provider("layer_height", value)
                self.assertEqual(native.call_count, 0)
                self.assertEqual(results[0]["argument_diagnostics"]["reason"], reason)
                self.assertEqual(results[0]["error"]["stage"], "validation")
                self.assertNotIn(marker, json.dumps(results))
        _, results, _, native = self.run_provider("layer_height", "0.12", {marker: integration.CONFIG["api_key"]})
        self.assertEqual(native.call_count, 0)
        self.assertNotIn(marker, json.dumps(results))
        self.assertNotIn(integration.CONFIG["api_key"], json.dumps(results))

    def test_public_version_uses_plugin_json_and_static_compatibility_marker(self):
        root = Path(__file__).resolve().parents[1] / "plugin" / "AIAssistant"
        metadata = json.loads((root / "plugin.json").read_text(encoding="utf8"))
        release = public_release()
        self.assertEqual(set(release), {"version", "release_id", "numeric_argument_policy"})
        self.assertEqual(release["version"], "0.1.1")
        self.assertEqual(release, {key: metadata[key] for key in release})
        script = (root / "web" / "app.js").read_text(encoding="utf8")
        self.assertEqual(re.search(r'UI_RELEASE = "([^"]+)"', script).group(1), release["release_id"])
        self.assertEqual(re.search(r'UI_NUMERIC_POLICY = "([^"]+)"', script).group(1), release["numeric_argument_policy"])
        agent = AgentService(self.adapter, lambda: dict(integration.CONFIG))
        self.addCleanup(agent.close)
        status = agent.get_status()
        self.assertEqual(status["plugin"], release)
        self.assertNotIn(integration.CONFIG["api_key"], json.dumps(status))


if __name__ == "__main__":
    unittest.main()
