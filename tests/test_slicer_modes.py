"""TASK 02: delayed backend events, real worker/main separation; no Qt runtime."""
import json
import queue
import threading
import time
import unittest
from urllib.request import Request, urlopen

import test_slicer_settings as settings_tests
from AIAssistant.agent.mcp import dispatch
from AIAssistant.agent.service import AgentService
from AIAssistant.agent.tools import ToolRegistry
from AIAssistant.server import create_server
from AIAssistant.slicer.adapter import SlicerAdapter


class Backend(object):
    def __init__(self, app, plugin_id):
        self.app, self.plugin_id = app, plugin_id

    def getPluginId(self):
        self.app.check_thread()
        return self.plugin_id


class Manager(object):
    def __init__(self, app):
        self.app = app
        # Deliberately unlike production IDs: verify that no table is hardcoded.
        self.backends = {mode: Backend(app, "registered-" + mode) for mode in
                         ("classic", "cylindrical", "cylindrical_full", "spherical")}
        self.current = self.backends["classic"]
        self.failure = None

    def getCurrentBackend(self):
        self.app.check_thread()
        return self.current

    def getBackendByType(self, mode):
        self.app.check_thread()
        if self.failure is not None:
            raise self.failure
        return self.backends.get(mode)


class ModeStack(settings_tests.Stack):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.mode = "classic"
        self.mode_supported = True
        self.five_axis = True
        self.options = {"classic": "3D runtime", "cylindrical": "Spiral runtime",
                        "cylindrical_full": "5D runtime", "spherical": "Alpha"}
        self.mode_writes = []
        self.ignore_mode_write = False
        self.failure_after_write = None

    def getId(self):
        self.check_thread()
        return "machine-runtime-id"

    def getName(self):
        self.check_thread()
        return "Machine runtime name"

    def getDefinition(self):
        self.check_thread()
        return self.app.definition

    def hasProperty(self, key, prop):
        if key == "printing_mode":
            self.check_thread()
            return self.mode_supported
        return super().hasProperty(key, prop)

    def getProperty(self, key, prop):
        if key == "printing_mode":
            self.check_thread()
            if prop == "value" and self.failure_after_write and self.mode_writes:
                raise self.failure_after_write
            return {"value": self.mode if self.mode_supported else None,
                    "type": "enum", "options": self.options}[prop]
        if key == "machine_five_axis":
            self.check_thread()
            return self.five_axis
        return super().getProperty(key, prop)

    def setProperty(self, key, prop, value):
        if key != "printing_mode":
            return super().setProperty(key, prop, value)
        self.check_thread()
        if self.app.write_wait is not None:
            if not self.app.write_wait.wait(2):
                raise AssertionError("Worker did not time out")
        self.mode_writes.append(value)
        if not self.ignore_mode_write:
            self.mode = value
            # Separate queue: the test decides when the native signal is handled.
            self.app.backend_events.put(lambda: self.app.apply_backend(value))


class Definition(object):
    def __init__(self, app):
        self.app = app

    def getId(self):
        self.app.check_thread()
        return "definition-runtime-id"


class ModeApp(settings_tests.App):
    def __init__(self):
        super().__init__()
        self.reads = []
        self.manager = Manager(self)
        self.definition = Definition(self)
        self.stack = ModeStack(self)
        self.backend_events = queue.Queue()
        self.write_wait = None

    def check_thread(self):
        self.reads.append(threading.current_thread())
        if threading.current_thread() is not self.main:
            raise AssertionError("API accessed outside main thread")

    def getVersion(self):
        self.check_thread()
        return "version-from-runtime"

    def getBackendManager(self):
        self.check_thread()
        return self.manager

    def apply_backend(self, mode):
        self.check_thread()
        self.manager.current = self.manager.backends.get(mode)


class ModeTests(unittest.TestCase):
    worker = settings_tests.SettingsTests.worker
    finish = settings_tests.SettingsTests.finish

    def setUp(self):
        self.app = ModeApp()
        self.adapter = SlicerAdapter(self.app, timeout=0.25)
        self.tools = ToolRegistry(self.adapter)

    @staticmethod
    def request(name, arguments):
        return {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}

    def rpc(self, name, arguments):
        return dispatch(self.request(name, arguments), self.tools)

    def pump_until_done(self, worker):
        deadline = time.monotonic() + 2
        while not worker[3].is_set() and time.monotonic() < deadline:
            try:
                callback = self.app.callbacks.get(timeout=0.01)
            except queue.Empty:
                continue
            callback()
        return self.finish(worker)

    def start_switch(self, mode="cylindrical"):
        worker = self.worker(lambda: self.adapter.set_print_mode(mode))
        self.app.callbacks.get(timeout=1)()
        return worker

    def assert_error(self, result, code):
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["code"], code)

    def test_state_reads_runtime_data_on_main_thread(self):
        worker = self.worker(self.adapter.get_slicer_state)
        callback = self.app.callbacks.get(timeout=1)
        self.assertEqual(self.app.reads, [])
        self.assertEqual(self.app.stack.accesses, [])
        callback()
        result = self.finish(worker)
        self.assertEqual(result, {"ok": True, "data": {
            "connected": True, "application_version": "version-from-runtime",
            "machine": {"id": "machine-runtime-id", "name": "Machine runtime name", "definition_id": "definition-runtime-id"},
            "printing_mode": "classic", "backend": {"id": "registered-classic", "expected_id": "registered-classic", "matches_mode": True},
            "available_modes": [
                {"id": "classic", "label": "3D runtime", "backend_available": True},
                {"id": "cylindrical", "label": "Spiral runtime", "backend_available": True},
                {"id": "cylindrical_full", "label": "5D runtime", "backend_available": True}]}})
        self.assertTrue(all(t is self.app.main for t in self.app.reads + self.app.stack.accesses))
        json.dumps(result, allow_nan=False)

    def test_state_without_machine_is_successful_diagnostic(self):
        self.app.stack = None
        result = self.adapter.get_slicer_state()
        self.assertEqual(result["data"], {"connected": True, "application_version": "version-from-runtime",
                                         "machine": None, "printing_mode": None, "backend": None, "available_modes": []})
        self.assertTrue(result["ok"])

    def test_state_missing_backend_or_manager_has_null_ids(self):
        self.app.manager.current = None
        self.app.manager.backends.pop("classic")
        state = self.adapter.get_slicer_state()["data"]
        self.assertEqual(state["backend"], {"id": None, "expected_id": None, "matches_mode": False})
        self.assertFalse(state["available_modes"][0]["backend_available"])
        self.app.manager = None
        state = self.adapter.get_slicer_state()["data"]
        self.assertTrue(all(not mode["backend_available"] for mode in state["available_modes"]))

    def test_unknown_current_mode_is_preserved_and_not_offered(self):
        for mode in ("spherical", "new-runtime-mode"):
            self.app.stack.mode = mode
            state = self.adapter.get_slicer_state()["data"]
            self.assertEqual(state["printing_mode"], mode)
            self.assertNotIn(mode, [item["id"] for item in state["available_modes"]])
        self.assertIsNone(state["backend"]["expected_id"])

    def test_same_id_different_backend_object_does_not_match(self):
        self.app.manager.current = Backend(self.app, "registered-classic")
        state = self.adapter.get_slicer_state()["data"]
        self.assertEqual(state["backend"]["id"], state["backend"]["expected_id"])
        self.assertFalse(state["backend"]["matches_mode"])

    def test_machine_capability_filters_available_modes(self):
        self.app.stack.five_axis = False
        self.assertEqual([m["id"] for m in self.adapter.get_slicer_state()["data"]["available_modes"]], ["classic"])
        self.app.stack.options = {}
        self.assertEqual(self.adapter.get_slicer_state()["data"]["available_modes"], [])

    def test_three_modes_confirm_only_after_delayed_backend_event(self):
        for mode in ("cylindrical", "cylindrical_full", "classic"):
            before = self.app.stack.mode
            worker = self.start_switch(mode)
            self.assertEqual(self.app.stack.mode, mode)
            self.assertIsNot(self.app.manager.current, self.app.manager.backends[mode])
            # One actual confirmation event before native backend change.
            self.app.callbacks.get(timeout=1)()
            self.assertFalse(worker[3].is_set())
            self.app.backend_events.get(timeout=1)()
            result = self.pump_until_done(worker)
            self.assertEqual(result, {"ok": True, "data": {"before": before, "after": mode,
                "backend": {"id": "registered-" + mode, "expected_id": "registered-" + mode, "matches_mode": True}}})
        self.assertEqual(self.app.stack.mode_writes, ["cylindrical", "cylindrical_full", "classic"])

    def test_backend_never_switches_returns_error_with_state_instruction(self):
        result = self.pump_until_done(self.start_switch())
        self.assert_error(result, "backend_not_switched")
        self.assertIn("get_slicer_state", result["error"]["message"])
        self.assertEqual(self.app.stack.mode_writes, ["cylindrical"])
        self.assertEqual(self.app.stack.mode, "cylindrical")

    def test_ignored_setting_write_is_not_success(self):
        self.app.stack.ignore_mode_write = True
        result = self.pump_until_done(self.start_switch())
        self.assert_error(result, "mode_not_applied")
        self.assertIn("get_slicer_state", result["error"]["message"])

    def test_idempotent_mode_skips_write_and_backend_restart(self):
        worker = self.start_switch("classic")
        result = self.finish(worker)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["before"], "classic")
        self.assertEqual(self.app.stack.mode_writes, [])
        self.assertTrue(self.app.backend_events.empty())

    def test_same_mode_with_wrong_backend_is_not_false_success(self):
        self.app.manager.current = self.app.manager.backends["cylindrical"]
        result = self.pump_until_done(self.start_switch("classic"))
        self.assert_error(result, "backend_not_switched")
        self.assertEqual(self.app.stack.mode_writes, [])

    def test_main_thread_switch_rejected_before_write(self):
        self.assert_error(self.adapter.set_print_mode("cylindrical"), "slicer_error")
        self.assertEqual(self.app.stack.mode_writes, [])
        self.assertTrue(self.app.callbacks.empty())

    def test_validation_precedes_any_api_access(self):
        for arguments in ({}, {"mode": "discrete"}, {"mode": "spherical"}, {"mode": "unknown"},
                          {"mode": True}, {"mode": None}, {"mode": []}, {"mode": 1},
                          {"mode": "classic", "extra": 1}):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.rpc("set_print_mode", arguments)["error"]["code"], -32602)
        self.assertEqual(self.rpc("get_slicer_state", {"extra": 1})["error"]["code"], -32602)
        self.assertEqual(self.app.stack.accesses, [])
        self.assertEqual(self.app.reads, [])

    def test_unavailable_machine_mode_and_backend_prevent_write(self):
        for scenario, code in (("no_machine", "no_active_machine"), ("no_setting", "setting_unavailable"),
                               ("no_axes", "mode_unavailable"), ("no_enum", "mode_unavailable"),
                               ("no_backend", "backend_unavailable")):
            with self.subTest(scenario=scenario):
                self.setUp()
                stack = self.app.stack
                if scenario == "no_machine":
                    self.app.stack = None
                elif scenario == "no_setting":
                    stack.mode_supported = False
                elif scenario == "no_axes":
                    stack.five_axis = False
                elif scenario == "no_enum":
                    stack.options.pop("cylindrical")
                else:
                    self.app.manager.backends.pop("cylindrical")
                self.assert_error(self.finish(self.start_switch()), code)
                self.assertEqual(stack.mode_writes, [])

    def test_timeout_before_callback_prevents_late_write(self):
        worker = self.worker(lambda: self.adapter.set_print_mode("cylindrical"))
        late = self.app.callbacks.get(timeout=1)
        self.assert_error(self.finish(worker), "qt_timeout")
        late()
        self.assertEqual(self.app.stack.mode_writes, [])

    def test_timeout_after_write_started_reports_uncertainty_no_retry(self):
        worker = self.worker(lambda: self.adapter.set_print_mode("cylindrical"))
        self.app.write_wait = worker[3]
        self.app.callbacks.get(timeout=1)()
        result = self.finish(worker)
        self.assert_error(result, "qt_timeout")
        self.assertIn("get_slicer_state", result["error"]["message"])
        self.assertEqual(self.app.stack.mode_writes, ["cylindrical"])

    def test_single_budget_includes_wait_for_start(self):
        # Scheduler delay plus backend wait must share one budget, not two.
        began = time.monotonic()
        worker = self.worker(lambda: self.adapter.set_print_mode("cylindrical"))
        first = self.app.callbacks.get(timeout=1)
        time.sleep(0.17)
        first()
        self.assert_error(self.pump_until_done(worker), "backend_not_switched")
        self.assertLess(time.monotonic() - began, 0.36)

    def test_shutdown_cancels_pending_and_rejects_new_operations(self):
        worker = self.worker(lambda: self.adapter.set_print_mode("cylindrical"))
        late = self.app.callbacks.get(timeout=1)
        self.adapter.close()
        self.assert_error(self.finish(worker), "slicer_not_connected")
        late()
        self.assert_error(self.adapter.get_slicer_state(), "slicer_not_connected")
        self.assert_error(self.adapter.set_print_mode("classic"), "slicer_not_connected")
        self.assertEqual(self.app.stack.mode_writes, [])

    def test_shutdown_during_backend_confirmation(self):
        worker = self.start_switch()
        self.app.callbacks.get(timeout=1)()
        self.adapter.close()
        result = self.finish(worker)
        self.assert_error(result, "slicer_not_connected")
        self.assertIn("get_slicer_state", result["error"]["message"])
        self.assertEqual(self.app.stack.mode_writes, ["cylindrical"])

    def test_parallel_switch_is_rejected_and_cannot_confirm_other_request(self):
        first = self.start_switch()
        second = self.worker(lambda: self.adapter.set_print_mode("cylindrical_full"))
        self.assert_error(self.finish(second), "switch_in_progress")
        self.app.backend_events.get(timeout=1)()
        self.assertTrue(self.pump_until_done(first)["ok"])
        self.assertEqual(self.app.stack.mode_writes, ["cylindrical"])
        # Lock has been released after completion.
        self.assertTrue(self.finish(self.start_switch("cylindrical"))["ok"])

    def test_machine_changed_during_confirmation_is_diagnosed(self):
        worker = self.start_switch()
        self.app.stack = ModeStack(self.app)
        self.assert_error(self.pump_until_done(worker), "mode_not_applied")

    def test_api_exception_during_confirmation_is_mcp_error_not_unknown_tool(self):
        self.app.stack.failure_after_write = KeyError("private API data")
        worker = self.worker(lambda: self.rpc("set_print_mode", {"mode": "cylindrical"}))
        self.app.callbacks.get(timeout=1)()
        with self.assertLogs("AIAssistant.slicer.adapter", level="ERROR") as captured:
            response = self.pump_until_done(worker)
        self.assertTrue(response["result"]["isError"])
        result = json.loads(response["result"]["content"][0]["text"])
        self.assert_error(result, "slicer_error")
        self.assertIn("get_slicer_state", result["error"]["message"])
        self.assertNotIn("private API data", "".join(captured.output))

    def test_registry_schema_and_dev_mode(self):
        tools = self.tools.list_tools()
        self.assertEqual([t["name"] for t in tools], ["assistant_status", "get_setting", "set_setting", "get_slicer_state", "set_print_mode", "list_quality_profiles", "list_materials", "select_profile", "select_material", "load_model", "list_models", "get_model_bounds", "start_slicing", "get_slicing_status", "get_slicing_result", "export_gcode", "save_project_3mf", "get_export_status", "describe_settings", "get_model_state", "move_model", "rotate_model", "scale_model", "delete_model", "arrange_models", "get_arrangement_status", "load_model_file", "list_custom_profiles", "create_custom_profile", "select_custom_profile", "save_custom_profile"])
        self.assertEqual(tools[4]["inputSchema"], {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["classic", "cylindrical", "cylindrical_full"]}},
            "required": ["mode"], "additionalProperties": False})
        dev = AgentService().tools
        for name, arguments in (("get_slicer_state", {}), ("set_print_mode", {"mode": "classic"})):
            response = dispatch(self.request(name, arguments), dev)
            self.assertTrue(response["result"]["isError"])
            self.assert_error(json.loads(response["result"]["content"][0]["text"]), "slicer_not_connected")
        self.assertFalse(dev.call("assistant_status", {})["runtime_verified"])

    def test_http_switch_waits_for_native_event_and_state_reports_actual_backend(self):
        server = create_server(AgentService(self.adapter))
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        try:
            def post(name, arguments):
                body = json.dumps(self.request(name, arguments)).encode("utf-8")
                request = Request("http://127.0.0.1:%d/mcp" % server.server_port, data=body,
                                  headers={"Content-Type": "application/json"})
                with urlopen(request, timeout=3) as response:
                    return json.loads(response.read().decode("utf-8"))
            worker = self.worker(lambda: post("set_print_mode", {"mode": "cylindrical_full"}))
            self.app.callbacks.get(timeout=1)()
            self.app.callbacks.get(timeout=1)()
            self.assertFalse(worker[3].is_set())
            self.app.backend_events.get(timeout=1)()
            response = self.pump_until_done(worker)
            self.assertFalse(response["result"]["isError"])
            worker = self.worker(lambda: post("get_slicer_state", {}))
            self.app.callbacks.get(timeout=1)()
            response = self.finish(worker)
            state = json.loads(response["result"]["content"][0]["text"])["data"]
            self.assertEqual(state["printing_mode"], "cylindrical_full")
            self.assertEqual(state["backend"]["id"], "registered-cylindrical_full")
            self.assertTrue(state["backend"]["matches_mode"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == "__main__":
    unittest.main()
