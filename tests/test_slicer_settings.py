"""Isolated TASK 01 checks; the queue is not a substitute for a Qt runtime."""
import ast
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import unittest
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin"))

from AIAssistant.agent.mcp import dispatch
from AIAssistant.agent.service import AgentService
from AIAssistant.agent.tools import ToolRegistry
from AIAssistant.server import create_server
from AIAssistant.slicer.adapter import SlicerAdapter
from AIAssistant.slicer.qt_bridge import QtBridge


class Stack(object):
    def __init__(self):
        self.main = threading.current_thread()
        self.value = 0.2
        self.minimum = 0.001
        self.maximum = None
        self.supported = True
        self.ignore_write = False
        self.failure = None
        self.writes = []
        self.accesses = []
        self.write_wait = None

    def check_thread(self):
        current = threading.current_thread()
        self.accesses.append(current)
        if current is not self.main:
            raise AssertionError("Stack accessed outside main thread")

    def hasProperty(self, key, prop):
        self.check_thread()
        if self.failure is not None:
            raise self.failure
        return self.supported and key == "layer_height" and prop in (
            "value", "minimum_value", "maximum_value", "minimum_value_warning", "maximum_value_warning",
            "type", "unit", "enabled", "settable_globally", "settable_per_extruder", "options")

    def getProperty(self, key, prop):
        self.check_thread()
        return {"value": self.value, "minimum_value": self.minimum,
                "maximum_value": self.maximum, "minimum_value_warning": None, "maximum_value_warning": None,
                "type": "float", "unit": "mm", "enabled": True, "settable_globally": True,
                "settable_per_extruder": False, "options": {}}[prop]

    def getReadOnly(self):
        self.check_thread()
        return False

    @property
    def userChanges(self):
        self.check_thread()
        return self

    def setProperty(self, key, prop, value):
        self.check_thread()
        if self.write_wait is not None:
            if not self.write_wait.wait(2):
                raise AssertionError("Worker did not time out")
        self.writes.append(value)
        if not self.ignore_write:
            self.value = value


class App(object):
    def __init__(self):
        self.main = threading.current_thread()
        self.stack = Stack()
        self.callbacks = queue.Queue()
        self.stack_calls = []
        self.scheduler_error = None

    def getMainThread(self):
        if threading.current_thread() is not self.main:
            raise AssertionError("getMainThread called by worker")
        return self.main

    def callLater(self, callback):
        if self.scheduler_error:
            raise self.scheduler_error
        self.callbacks.put(callback)

    def getGlobalContainerStack(self):
        current = threading.current_thread()
        if current is not self.main:
            raise AssertionError("App accessed outside main thread")
        self.stack_calls.append(current)
        return self.stack


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.app = App()
        self.adapter = SlicerAdapter(self.app, timeout=0.1)
        self.tools = ToolRegistry(self.adapter)

    def worker(self, callback):
        results, errors = [], []
        done = threading.Event()

        def run():
            try:
                results.append(callback())
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()
        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(thread.join, 2)
        return thread, results, errors, done

    def finish(self, worker):
        thread, results, errors, done = worker
        self.assertTrue(done.wait(2))
        thread.join(2)
        self.assertFalse(errors, errors)
        return results[0]

    def rpc(self, name="get_setting", arguments=None):
        if arguments is None:
            arguments = {"key": "layer_height", "scope": "global"}
        return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}

    def dispatch(self, request):
        return dispatch(request, self.tools)

    def decoded(self, response):
        return json.loads(response["result"]["content"][0]["text"])

    def test_worker_schedules_without_touching_stack_then_reads(self):
        worker = self.worker(lambda: self.adapter.get_setting("layer_height", "global"))
        callback = self.app.callbacks.get(timeout=1)
        self.assertEqual(self.app.stack_calls, [])
        self.assertEqual(self.app.stack.accesses, [])
        self.assertFalse(worker[3].is_set())
        callback()
        self.assertEqual(self.finish(worker), {"ok": True, "data": {
            "key": "layer_height", "scope": "global", "value": 0.2, "unit": "mm"}})

    def test_worker_write_confirm_and_restore(self):
        for value, before in ((0.24, 0.2), (0.2, 0.24)):
            worker = self.worker(lambda: self.adapter.set_setting("layer_height", "global", value))
            self.app.callbacks.get(timeout=1)()
            self.assertEqual(self.finish(worker), {"ok": True, "data": {
                "key": "layer_height", "scope": "global", "before": before,
                "after": value, "unit": "mm"}})
            self.assertEqual(self.adapter.get_setting("layer_height", "global")["data"]["value"], value)
        self.assertTrue(all(t is self.app.main for t in self.app.stack.accesses))

    def test_main_thread_is_direct_and_does_not_deadlock(self):
        self.assertTrue(self.adapter.set_setting("layer_height", "global", 0.24)["ok"])
        self.assertTrue(self.app.callbacks.empty())

    def test_invalid_arguments_are_protocol_errors_before_scheduling(self):
        valid = {"key": "layer_height", "scope": "global", "value": 0.24}
        cases = [{}, {"key": "layer_height"}, dict(valid, scope="extruder"),
                 dict(valid, key="machine_start_gcode"), dict(valid, extra=1)]
        for value in (True, False, "0.2", None, float("nan"), float("inf"),
                      -float("inf"), 0, -0.2, 10 ** 1000):
            cases.append(dict(valid, value=value))
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(self.dispatch(self.rpc("set_setting", arguments))["error"]["code"], -32602)
        for arguments in ({"key": "layer_height"}, dict(valid),
                          {"key": [], "scope": "global"}, {"key": "layer_height", "scope": None}):
            self.assertEqual(self.dispatch(self.rpc(arguments=arguments))["error"]["code"], -32602)
        self.assertEqual(self.app.stack.accesses, [])
        self.assertTrue(self.app.callbacks.empty())

    def test_current_hard_bounds_and_inclusive_boundary(self):
        self.app.stack.minimum, self.app.stack.maximum = 0.12, 0.3
        for value in (0.1, 0.31):
            result = self.adapter.set_setting("layer_height", "global", value)
            self.assertEqual(result["error"]["code"], "setting_out_of_range")
        self.assertEqual(self.app.stack.writes, [])
        for value in (0.12, 0.3):
            self.assertTrue(self.adapter.set_setting("layer_height", "global", value)["ok"])

    def test_absent_hard_bounds_do_not_prevent_write(self):
        self.app.stack.minimum = None
        self.assertTrue(self.adapter.set_setting("layer_height", "global", 0.02)["ok"])

    def test_unreadable_bound_prevents_write(self):
        self.app.stack.minimum = "unresolved formula"
        self.assertEqual(self.adapter.set_setting("layer_height", "global", 0.24)["error"]["code"], "slicer_error")
        self.assertEqual(self.app.stack.writes, [])

    def test_missing_machine_and_unsupported_setting(self):
        stack = self.app.stack
        self.app.stack = None
        self.assertEqual(self.adapter.get_setting("layer_height", "global")["error"]["code"], "no_active_machine")
        self.app.stack = stack
        stack.supported = False
        self.assertEqual(self.adapter.set_setting("layer_height", "global", 0.24)["error"]["code"], "setting_unavailable")
        self.assertEqual(stack.writes, [])

    def test_non_numeric_api_value_never_leaks_as_json(self):
        for value in (object(), True, "0.2", None, float("nan"), float("inf")):
            self.app.stack.value = value
            result = self.dispatch(self.rpc())
            self.assertEqual(self.decoded(result)["error"]["code"], "setting_unavailable")
            self.assertTrue(result["result"]["isError"])
            json.dumps(result, allow_nan=False)

    def test_ignored_write_is_not_success(self):
        self.app.stack.ignore_write = True
        result = self.dispatch(self.rpc("set_setting", {"key": "layer_height", "scope": "global", "value": 0.24}))
        self.assertEqual(self.decoded(result)["error"]["code"], "setting_not_applied")
        self.assertTrue(result["result"]["isError"])

    def test_pending_timeout_cancels_late_write(self):
        worker = self.worker(lambda: self.adapter.set_setting("layer_height", "global", 0.24))
        callback = self.app.callbacks.get(timeout=1)
        result = self.finish(worker)
        self.assertEqual(result["error"]["code"], "qt_timeout")
        self.assertIn("отменён", result["error"]["message"])
        callback()
        self.assertEqual(self.app.stack.writes, [])
        self.assertEqual(self.app.stack_calls, [])

    def test_running_timeout_reports_uncertainty_without_retry(self):
        worker = self.worker(lambda: self.adapter.set_setting("layer_height", "global", 0.24))
        self.app.stack.write_wait = worker[3]
        self.app.callbacks.get(timeout=1)()
        result = self.finish(worker)
        self.assertEqual(result["error"]["code"], "qt_timeout")
        self.assertIn("Перечитайте", result["error"]["message"])
        self.assertEqual(self.app.stack.writes, [0.24])

    def test_close_cancels_pending_and_rejects_new_calls(self):
        worker = self.worker(lambda: self.adapter.set_setting("layer_height", "global", 0.24))
        callback = self.app.callbacks.get(timeout=1)
        self.adapter.close()
        self.assertEqual(self.finish(worker)["error"]["code"], "slicer_not_connected")
        callback()
        self.assertEqual(self.adapter.get_setting("layer_height", "global")["error"]["code"], "slicer_not_connected")
        self.assertEqual(self.app.stack.accesses, [])

    def test_original_callback_exception_preserved_by_bridge(self):
        bridge = QtBridge(self.app, timeout=0.5)
        original = KeyError("internal key")

        def fail(app):
            raise original
        worker = self.worker(lambda: bridge.run(fail))
        self.app.callbacks.get(timeout=1)()
        self.assertTrue(worker[3].wait(1))
        self.assertIs(worker[2][0], original)

    def test_worker_api_exception_is_mcp_operational_error_not_unknown_tool(self):
        self.app.stack.failure = KeyError("private value must not be logged")
        worker = self.worker(lambda: self.dispatch(self.rpc()))
        with self.assertLogs("AIAssistant.slicer.adapter", level="ERROR") as captured:
            self.app.callbacks.get(timeout=1)()
            result = self.finish(worker)
        self.assertEqual(self.decoded(result)["error"]["code"], "slicer_error")
        self.assertTrue(result["result"]["isError"])
        self.assertIn("KeyError", "".join(captured.output))
        self.assertNotIn("private value must not be logged", "".join(captured.output))

    def test_scheduler_failure_returns_api_error(self):
        self.app.scheduler_error = RuntimeError("scheduler failed")
        with self.assertLogs("AIAssistant.slicer.adapter", level="ERROR"):
            result = self.finish(self.worker(lambda: self.adapter.get_setting("layer_height", "global")))
        self.assertEqual(result["error"]["code"], "slicer_error")

    def test_registry_and_protocol(self):
        tools = self.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertEqual(tools, self.tools.list_tools())
        self.assertEqual([t["name"] for t in tools], ["assistant_status", "get_setting", "set_setting", "get_slicer_state", "set_print_mode", "list_quality_profiles", "list_materials", "select_profile", "select_material", "load_model", "list_models", "get_model_bounds", "start_slicing", "get_slicing_status", "get_slicing_result", "export_gcode", "save_project_3mf", "get_export_status", "describe_settings", "get_model_state", "move_model", "rotate_model", "scale_model", "delete_model", "arrange_models", "get_arrangement_status", "load_model_file", "list_custom_profiles", "create_custom_profile", "select_custom_profile", "save_custom_profile", "wait_for_slicing", "wait_for_export", "center_model"])
        schema = tools[2]["inputSchema"]
        self.assertEqual(set(schema["required"]), {"key", "scope", "value"})
        self.assertEqual(schema["properties"]["value"]["type"], ["number", "boolean", "string"])
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(self.dispatch(self.rpc())["result"]["isError"])
        self.assertEqual(self.dispatch(self.rpc("unknown", {}))["error"]["message"], "Unknown tool")
        self.assertEqual(self.dispatch(self.rpc(arguments=[]))["error"]["code"], -32602)
        for request in ([], {"jsonrpc": "1.0", "id": 1, "method": "tools/list"}):
            self.assertEqual(self.dispatch(request)["error"]["code"], -32600)
        self.assertIsNone(self.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        initialized = self.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertIn("tools", initialized["result"]["capabilities"])

    def test_disconnected_dev_mode_is_explicit(self):
        service = AgentService()
        for name, arguments in (("get_setting", {"key": "layer_height", "scope": "global"}),
                                ("set_setting", {"key": "layer_height", "scope": "global", "value": 0.24})):
            result = dispatch(self.rpc(name, arguments), service.tools)
            self.assertEqual(self.decoded(result)["error"]["code"], "slicer_not_connected")
            self.assertTrue(result["result"]["isError"])
        status = service.tools.call("assistant_status", {})
        self.assertFalse(status["llm_connected"])
        self.assertFalse(status["runtime_verified"])

    def test_http_mcp_uses_injected_adapter_and_preserves_errors(self):
        server = create_server(AgentService(self.adapter))
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        try:
            def post(request):
                body = json.dumps(request).encode("utf-8")
                req = Request("http://127.0.0.1:%d/mcp" % server.server_port,
                              data=body, headers={"Content-Type": "application/json"})
                with urlopen(req, timeout=3) as response:
                    return json.loads(response.read().decode("utf-8"))
            worker = self.worker(lambda: post(self.rpc("set_setting", {
                "key": "layer_height", "scope": "global", "value": 0.24})))
            self.app.callbacks.get(timeout=1)()
            result = self.finish(worker)
            self.assertEqual(self.decoded(result)["data"]["after"], 0.24)
            self.assertFalse(result["result"]["isError"])
            self.app.stack.failure = KeyError("internal API key")
            worker = self.worker(lambda: post(self.rpc()))
            with self.assertLogs("AIAssistant.slicer.adapter", level="ERROR"):
                self.app.callbacks.get(timeout=1)()
                result = self.finish(worker)
            self.assertEqual(self.decoded(result)["error"]["code"], "slicer_error")
            self.assertTrue(result["result"]["isError"])
            self.assertEqual(post(self.rpc("set_setting", {
                "key": "layer_height", "scope": "global", "value": False}))["error"]["code"], -32602)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


class CompatibilityTests(unittest.TestCase):
    def test_python35_syntax_and_plugin_api(self):
        files = list((ROOT / "plugin" / "AIAssistant").rglob("*.py"))
        files += list((ROOT / "scripts").glob("*.py"))
        files += list((ROOT / "tests").glob("*.py"))
        for path in files:
            with self.subTest(path=str(path.relative_to(ROOT))):
                ast.parse(path.read_text(encoding="utf-8-sig"), feature_version=(3, 5))
        meta = json.loads((ROOT / "plugin" / "AIAssistant" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["api"], 5)

    def test_dev_server_starts_and_serves_mcp_without_qt(self):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev_server.py")],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            # Read output in a worker so a failed startup cannot hang the test.
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: lines.put(process.stdout.readline()))
            reader.daemon = True
            reader.start()
            line = lines.get(timeout=5).decode("utf-8").strip()
            self.assertTrue(line.startswith("Dev UI: http://127.0.0.1:"), line)
            base = line.rsplit(" ", 1)[1]
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "get_setting", "arguments": {"key": "layer_height", "scope": "global"}}}).encode("utf-8")
            with urlopen(Request(base + "mcp", data=body, headers={"Content-Type": "application/json"}), timeout=3) as response:
                result = json.loads(response.read().decode("utf-8"))
            self.assertTrue(result["result"]["isError"])
            self.assertEqual(json.loads(result["result"]["content"][0]["text"])["error"]["code"], "slicer_not_connected")
            self.assertNotIn("UM", sys.modules)
            self.assertNotIn("PyQt5", sys.modules)
        finally:
            process.terminate()
            process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
