"""Timeout diagnostics through real service/transport with fake clock/provider; no live requests."""
import copy
import json
import re
import ssl
import threading
import unittest
from unittest import mock

import test_agent_integration as integration
from AIAssistant.agent.llm import ChatCompletionsClient, LLMError


class TimeoutDiagnosticsTests(unittest.TestCase):
    worker, finish = integration.AgentTests.worker, integration.AgentTests.finish
    pump_until_done, drain = integration.AgentTests.pump_until_done, integration.AgentTests.drain
    service, send, snapshot, results = integration.AgentTests.service, integration.AgentTests.send, integration.AgentTests.snapshot, integration.AgentTests.results

    def setUp(self):
        integration.AgentTests.setUp(self)
        self.now = 0.0

    def fail_call(self, client, messages):
        budget = client.requests[-1][3]
        self.now += budget + 0.125
        raise LLMError("network_timeout", "private-provider-body https://user:password@provider.invalid secret-file.png " + integration.CONFIG["api_key"])

    def failure_numbers(self):
        errors = [e["data"] for e in self.agent.get_events(self.sid)["events"] if e["type"] == "error"]
        self.assertEqual(errors[-1]["code"], "network_timeout")
        message = errors[-1]["message"]
        self.assertEqual(self.snapshot()["messages"][-1]["text"], message)
        for private in (integration.CONFIG["api_key"], "private-provider-body", "password", "secret-file.png", "provider.invalid"):
            self.assertNotIn(private, message)
        # Numeric fields are delivered in the existing error message, no new telemetry schema.
        values = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", message)]
        return values, message

    def test_timeout_before_tools_reports_actual_call_budget_zero_changes_and_no_retry(self):
        self.service(self.fail_call)
        self.agent.clock = lambda: self.now
        self.send()
        values, message = self.failure_numbers()
        self.assertEqual(values[:5], [1, 60.125, 60, 300, 0])
        self.assertEqual(len(self.llm.requests), 1)
        self.assertEqual(self.llm.requests[0][3], 60)
        self.assertEqual(self.app.stack.writes, [])
        self.assertEqual(self.results(), [])
        self.assertEqual(self.snapshot()["agent_state"], "idle")

    def test_remaining_turn_budget_after_confirmed_write_keeps_result_no_second_write(self):
        def write(client, messages):
            self.now = 285.0
            return integration.calls(integration.tool("set_setting", {"key": "layer_height", "scope": "global", "value": 0.12}))
        self.service(write, self.fail_call)
        self.agent.clock = lambda: self.now
        with mock.patch.object(self.adapter, "set_setting", wraps=self.adapter.set_setting) as native:
            self.pump_until_done(self.worker(self.send))
            self.assertEqual(native.call_count, 1)
        values, message = self.failure_numbers()
        self.assertEqual(values[:5], [2, 15.125, 15, 15, 1])
        self.assertIn("set_setting", message)
        self.assertEqual([r[3] for r in self.llm.requests], [60, 15])
        self.assertEqual(self.app.stack.writes, [("layer_height", 0.12)])
        self.assertEqual(self.results()[0]["data"]["after"], 0.12)
        self.assertEqual(self.adapter.get_setting("layer_height", "global")["data"]["value"], 0.12)
        self.assertEqual(len(self.llm.requests), 2)

    def test_readonly_metadata_before_timeout_reports_third_call_not_mutation(self):
        self.service(integration.calls(integration.tool("get_setting", {"key": "layer_height", "scope": "global"})),
            integration.calls(integration.tool("describe_settings", {"scope": "global"}, "metadata")), self.fail_call)
        self.agent.clock = lambda: self.now
        self.pump_until_done(self.worker(self.send))
        values, message = self.failure_numbers()
        self.assertEqual(values[:5], [3, 60.125, 60, 300, 0])
        self.assertEqual(len(self.results()), 2)
        self.assertTrue(all(r["ok"] for r in self.results()))
        self.assertEqual(self.app.stack.writes, [])
        self.assertNotIn("minimum_value", message)

    def test_timeout_after_accepted_slice_does_not_claim_completed_cancel_or_repeat(self):
        self.service(integration.calls(integration.tool("start_slicing")), self.fail_call)
        self.agent.clock = lambda: self.now
        self.pump_until_done(self.worker(self.send))
        values, message = self.failure_numbers()
        self.assertEqual(values[:5], [2, 60.125, 60, 300, 1])
        self.assertIn("start_slicing", message)
        self.assertEqual(self.app.backend.calls, 1)
        self.assertTrue(self.app.backend._slicing)
        self.assertTrue(self.adapter._change_lock.locked())
        self.assertEqual(self.snapshot()["slice"]["state"], "running")
        self.assertIsNone(self.snapshot()["slice"]["result"])

    def test_late_transport_mutating_response_ignored_closed_and_no_tool_or_network_retry(self):
        release, entered, closed = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        # Windows trust-store initialization is not the delayed-response fixture.
        # Prepare TLS before the 30 ms test budget so the fake read is reached.
        tls_context = ssl.create_default_context()
        answer = json.dumps({"choices": [{"finish_reason": "stop", "message": integration.calls(integration.tool(
            "set_setting", {"key": "layer_height", "scope": "global", "value": 0.12}))}]}).encode("utf-8")

        class Response(object):
            def read(self, size):
                entered.set()
                if not release.wait(2):
                    raise AssertionError("Fake provider response was not released")
                return answer
            def close(self):
                closed.set()

        opens = []
        def open_fake(request, **kwargs):
            self.assertIs(kwargs["context"], tls_context)
            opens.append(kwargs["timeout"])
            return Response()
        client = ChatCompletionsClient(open_fake)
        self.addCleanup(client.close)
        self.service()
        self.agent._llm = client
        with mock.patch("AIAssistant.agent.service.SINGLE_REQUEST_TIMEOUT_SECONDS", 0.03), \
                mock.patch("AIAssistant.agent.llm.ssl.create_default_context", return_value=tls_context), \
                mock.patch.object(client, "_parse", wraps=client._parse) as parse:
            self.pump_until_done(self.worker(self.send))
            self.assertTrue(entered.is_set())
            self.assertEqual(opens, [0.03])
            before = copy.deepcopy(self.snapshot())
            values, message = self.failure_numbers()
            self.assertEqual(values[0], 1)
            self.assertEqual(values[2], 0.03)
            self.assertEqual(values[4], 0)
            self.assertGreaterEqual(values[1], 0.02)
            self.assertLess(values[1], 0.5)
            with self.assertRaises(LLMError) as caught:
                client.complete(integration.CONFIG, [], timeout=0.03)
            self.assertEqual(caught.exception.code, "request_busy")
            release.set()
            self.assertTrue(closed.wait(1))
            parse.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.app.stack.writes, [])
        self.assertEqual(opens, [0.03])
        self.assertEqual(self.results(), [])


if __name__ == "__main__":
    unittest.main()
