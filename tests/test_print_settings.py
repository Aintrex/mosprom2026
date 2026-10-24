"""TASK 07: runtime metadata and explicit extruder targets, not a native run."""
import json
import threading
import unittest
from unittest import mock

import test_slicer_slicing as slicing_tests
import test_slicer_modes as modes_tests
from AIAssistant.agent.mcp import dispatch
from AIAssistant.agent.tools import ToolRegistry
from AIAssistant.slicer.adapter import SlicerAdapter


class Stack(slicing_tests.Stack):
    def __init__(self, app, name="global"):
        super().__init__(app, name)
        self.readonly = False
        self.setter_failure = self.after_set = None
        self.props = {}
        for key, kind, value, unit in (("layer_height", "float", 0.2, "mm"),
                                       ("speed_print", "float", 50.0, "mm/s"),
                                       ("infill_sparse_density", "float", 20.0, "%"),
                                       ("wall_line_count", "int", 2, ""),
                                       ("support_enable", "bool", False, ""),
                                       ("infill_pattern", "enum", "grid", ""),
                                       ("material_print_temperature", "float", 205.0, "°C"),
                                       ("retraction_enable", "bool", True, "")):
            self.props[key] = {"type": kind, "value": value, "unit": unit, "enabled": True,
                               "settable_globally": True, "settable_per_extruder": key != "layer_height",
                               "minimum_value": 0 if kind in ("float", "int") else None, "maximum_value": None,
                               "minimum_value_warning": None, "maximum_value_warning": None,
                               "options": {"grid": "Translated Grid", "runtime_hex": "Translated Hex"} if kind == "enum" else {}}
        self.props["infill_sparse_density"].update(maximum_value=100, minimum_value_warning=10, maximum_value_warning=85)

    def hasProperty(self, key, prop):
        self.app.check_thread()
        if key == "printing_mode":
            return super().hasProperty(key, prop)
        return key in self.props and prop in self.props[key]

    def getProperty(self, key, prop):
        self.app.check_thread()
        if key == "printing_mode":
            return super().getProperty(key, prop)
        return self.props.get(key, {}).get(prop)

    def getAllKeys(self):
        self.app.check_thread()
        return set(self.props) | {"printing_mode"}

    def getReadOnly(self):
        self.app.check_thread()
        return self.readonly

    def setProperty(self, key, prop, value):
        self.app.check_thread()
        self.writes.append((key, value))
        if self.write_wait:
            if not self.write_wait.wait(2):
                raise AssertionError("No running timeout")
        if not self.ignore_write:
            self.props[key][prop] = value
            self.propertyChanged.emit(key, prop)
        if self.after_set:
            self.after_set()
        if self.setter_failure:
            raise self.setter_failure


class PrintSettingsTests(unittest.TestCase):
    worker = slicing_tests.SlicingTests.worker
    finish = slicing_tests.SlicingTests.finish
    pump_until_done = slicing_tests.SlicingTests.pump_until_done
    assert_error = slicing_tests.SlicingTests.assert_error
    start = slicing_tests.SlicingTests.start
    status = slicing_tests.SlicingTests.status
    complete = slicing_tests.SlicingTests.complete
    drain = slicing_tests.SlicingTests.drain

    def setUp(self):
        slicing_tests.SlicingTests.setUp(self)
        self.app.stack = Stack(self.app)
        self.app.stack.extruders = {p: Stack(self.app, "extruder-" + p) for p in ("0", "1")}
        self.app.ui_extruder = "1"

    def rpc(self, name, args):
        return dispatch(modes_tests.ModeTests.request(name, args), self.tools)

    def test_validation_before_api_scope_id_types_allowlist_and_integer_fraction(self):
        valid = {"key": "speed_print", "scope": "extruder", "extruder_id": "0", "value": 42}
        cases = [dict(valid, key="printing_mode"), dict(valid, key="machine_start_gcode"), dict(valid, key="material_print_temp"),
                 dict(valid, scope="global"), dict(valid, extruder_id=0), dict(valid, extruder_id=""),
                 dict(valid, extruder_id=None), dict(valid, extra=True), {"key": "speed_print", "scope": "extruder", "value": 42}]
        for value in (None, "42", True, [], {}, float("nan"), float("inf"), 10 ** 1000):
            cases.append(dict(valid, value=value))
        for value in (0, 1, "false", None):
            cases.append(dict(valid, key="support_enable", value=value))
        cases += [dict(valid, key="wall_line_count", value=2.5), dict(valid, key="infill_pattern", value="=__import__('os')"),
                  dict(valid, key="infill_pattern", value=3)]
        with mock.patch.object(self.app, "getGlobalContainerStack", side_effect=AssertionError("No API")):
            for args in cases:
                self.assertEqual(self.rpc("set_setting", args)["error"]["code"], -32602)
            for args in ({}, {"scope": "global", "extruder_id": "0"}, {"scope": "extruder"},
                         {"scope": "extruder", "extruder_id": 0}, {"scope": "global", "key": "speed_print"}):
                self.assertEqual(self.rpc("describe_settings", args)["error"]["code"], -32602)

    def test_registry_schema_and_dev_same_surface(self):
        tools = ToolRegistry()
        schemas = {tool["name"]: tool["inputSchema"] for tool in tools.list_tools()}
        schema = schemas["set_setting"]
        self.assertIn("speed_print", schema["properties"]["key"]["enum"])
        self.assertNotIn("printing_mode", schema["properties"]["key"]["enum"])
        self.assertEqual(schema["properties"]["scope"]["enum"], ["global", "extruder"])
        self.assertFalse(schema["additionalProperties"])
        for name, args in (("describe_settings", {"scope": "global"}),
                           ("get_setting", {"key": "speed_print", "scope": "extruder", "extruder_id": "0"}),
                           ("set_setting", {"key": "support_enable", "scope": "global", "value": True})):
            self.assert_error(tools.call(name, args), "slicer_not_connected")

    def test_legacy_shape_and_numeric_zero_int_float_bool_enum(self):
        result = self.adapter.set_setting("layer_height", "global", 0.24)
        self.assertEqual(result["data"], {"key": "layer_height", "scope": "global", "before": 0.2, "after": 0.24, "unit": "mm"})
        for key, value in (("speed_print", 35), ("wall_line_count", 3.0), ("support_enable", True), ("infill_pattern", "runtime_hex")):
            result = self.adapter.set_setting(key, "global", value)
            self.assertTrue(result["ok"], result)
            self.assertEqual(self.adapter.get_setting(key, "global")["data"]["value"], result["data"]["after"])
        self.assertIs(type(self.adapter.get_setting("wall_line_count", "global")["data"]["value"]), int)
        self.assertIs(type(self.adapter.get_setting("support_enable", "global")["data"]["value"]), bool)
        result = self.adapter.set_setting("infill_sparse_density", "global", 0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["after"], 0)
        self.assertTrue(result["data"]["warnings"])

    def test_explicit_extruder_target_keeps_other_and_ui_unchanged(self):
        for position, temperature in (("0", 210), ("1", 220)):
            result = self.adapter.set_setting("material_print_temperature", "extruder", temperature, extruder_id=position)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["data"]["extruder_id"], position)
        self.assertEqual(self.app.stack.extruders["0"].writes, [("material_print_temperature", 210.0)])
        self.assertEqual(self.app.stack.extruders["1"].writes, [("material_print_temperature", 220.0)])
        self.assertEqual(self.app.stack.writes, [])
        self.assertEqual(self.app.ui_extruder, "1")
        self.assertEqual(self.adapter.get_setting("material_print_temperature", "extruder", "0")["data"]["value"], 210.0)
        self.assert_error(self.adapter.get_setting("speed_print", "extruder", "UI label 1"), "extruder_not_found")

    def test_describe_has_actual_metadata_unavailable_keys_and_no_changes(self):
        result = self.adapter.describe_settings("extruder", "0", keys=["material_print_temperature", "layer_height", "line_width", "infill_pattern", "infill_sparse_density"])
        self.assertTrue(result["ok"], result)
        items = {item["key"]: item for item in result["data"]["settings"]}
        self.assertEqual(items["material_print_temperature"]["unit"], "°C")
        self.assertTrue(items["material_print_temperature"]["settable"])
        self.assertFalse(items["layer_height"]["settable"])
        self.assertFalse(items["line_width"]["available"])
        self.assertIsNone(items["line_width"]["value"])
        self.assertEqual(items["infill_pattern"]["options"]["runtime_hex"], "Translated Hex")
        self.assertEqual(items["infill_sparse_density"]["maximum_value_warning"], 85)
        json.dumps(result, allow_nan=False)
        self.assertEqual(self.app.ui_extruder, "1")
        self.assertTrue(all(not stack.writes for stack in [self.app.stack] + list(self.app.stack.extruders.values())))

    def test_runtime_options_keys_only_and_refresh_between_calls(self):
        self.assert_error(self.adapter.set_setting("infill_pattern", "global", "Translated Hex"), "setting_option_unavailable")
        self.assert_error(self.adapter.set_setting("infill_pattern", "global", "gyroid"), "setting_option_unavailable")
        self.app.stack.props["infill_pattern"]["options"]["gyroid"] = "Runtime New"
        self.assertTrue(self.adapter.set_setting("infill_pattern", "global", "gyroid")["ok"])
        self.assertEqual(self.app.stack.writes, [("infill_pattern", "gyroid")])

    def test_hard_and_warning_bounds_runtime_changes_inclusive_and_unresolved(self):
        props = self.app.stack.props["infill_sparse_density"]
        for value in (-1, 101):
            self.assert_error(self.adapter.set_setting("infill_sparse_density", "global", value), "setting_out_of_range")
        self.assertEqual(self.app.stack.writes, [])
        for value in (0, 100):
            result = self.adapter.set_setting("infill_sparse_density", "global", value)
            self.assertTrue(result["ok"])
            self.assertTrue(result["data"]["warnings"])
        props["maximum_value"] = 60
        self.assert_error(self.adapter.set_setting("infill_sparse_density", "global", 70), "setting_out_of_range")
        props["maximum_value"] = "not evaluated"
        self.assert_error(self.adapter.set_setting("infill_sparse_density", "global", 20), "slicer_error")
        props.update(maximum_value=100, minimum_value_warning="not evaluated")
        result = self.adapter.set_setting("infill_sparse_density", "global", 20)
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["warnings"])

    def test_missing_metadata_null_and_writes_refused_without_invented_defaults(self):
        props = self.app.stack.props["speed_print"]
        props.update(type="str", value="arbitrary text")
        items = self.adapter.describe_settings("global", keys=["speed_print"])["data"]["settings"]
        item = next(item for item in items if item["key"] == "speed_print")
        self.assertEqual(item["type"], "str")
        self.assertIsNone(item["value"])
        self.assert_error(self.adapter.set_setting("speed_print", "global", 40), "setting_metadata_unavailable")
        props.update(type="float", value=50)
        del props["enabled"]
        self.assert_error(self.adapter.set_setting("speed_print", "global", 40), "setting_metadata_unavailable")
        props["enabled"] = True
        props["settable_globally"] = None
        self.assert_error(self.adapter.set_setting("speed_print", "global", 40), "setting_metadata_unavailable")
        self.assertEqual(self.app.stack.writes, [])

    def test_disabled_scope_permission_readonly_and_missing_runtime_setting(self):
        props = self.app.stack.props["speed_print"]
        props["enabled"] = False
        self.assert_error(self.adapter.set_setting("speed_print", "global", 40), "setting_disabled")
        self.assertEqual(self.adapter.get_setting("speed_print", "global")["data"]["value"], 50)
        props.update(enabled=True, settable_globally=False)
        self.assert_error(self.adapter.set_setting("speed_print", "global", 40), "setting_scope_unavailable")
        props["settable_globally"] = True
        self.app.stack.readonly = True
        self.assert_error(self.adapter.set_setting("speed_print", "global", 40), "setting_read_only")
        self.assert_error(self.adapter.get_setting("line_width", "global"), "setting_unavailable")
        self.app.stack = None
        self.assert_error(self.adapter.describe_settings("global"), "no_active_machine")

    def test_noop_preserves_completed_slice_real_change_and_uncertain_setter_invalidate(self):
        self.complete()
        self.assertTrue(self.adapter.set_setting("speed_print", "global", 50)["ok"])
        self.assertEqual(self.app.stack.writes, [])
        self.assertTrue(self.status()["valid"])
        self.assertTrue(self.adapter.set_setting("material_print_temperature", "extruder", 210, "0")["ok"])
        self.assert_error(self.adapter.get_slicing_result(self.slice_id), "slice_result_stale")
        self.complete()
        self.app.stack.setter_failure = RuntimeError("private setting detail")
        with self.assertLogs("AIAssistant.slicer.adapter", level="ERROR") as captured:
            response = self.adapter.set_setting("speed_print", "global", 55)
        self.assert_error(response, "slicer_error")
        self.assertNotIn("private setting detail", "".join(captured.output))
        self.assert_error(self.adapter.get_slicing_result(self.slice_id), "slice_result_stale")

    def test_setter_ignored_or_context_replaced_is_not_confirmed(self):
        self.app.stack.ignore_write = True
        self.assert_error(self.adapter.set_setting("speed_print", "global", 40), "setting_not_applied")
        self.app.stack.ignore_write = False
        self.app.stack.after_set = lambda: setattr(self.app, "stack", Stack(self.app))
        self.assert_error(self.adapter.set_setting("speed_print", "global", 40), "setting_context_changed")

    def test_worker_qt_busy_pending_timeout_and_shutdown(self):
        stack = self.app.stack.extruders["0"]
        worker = self.worker(lambda: self.adapter.set_setting("speed_print", "extruder", 45, "0"))
        self.assertEqual(stack.writes, [])
        self.app.callbacks.get(timeout=1)()
        self.assertTrue(self.finish(worker)["ok"])
        self.adapter._change_lock.acquire(False)
        try:
            self.assert_error(self.adapter.set_setting("support_enable", "global", True), "slicing_busy")
            self.assertTrue(self.adapter.describe_settings("extruder", "0")["ok"])
        finally:
            self.adapter._change_lock.release()
        self.adapter._bridge._timeout = 0.01
        worker = self.worker(lambda: self.adapter.set_setting("speed_print", "extruder", 48, "0"))
        callback = self.app.callbacks.get(timeout=1)
        self.assert_error(self.finish(worker), "qt_timeout")
        callback()
        self.assertEqual(stack.props["speed_print"]["value"], 45)
        self.adapter.close()
        self.assert_error(self.adapter.describe_settings("global"), "slicer_not_connected")

    def test_running_write_timeout_keeps_uncertainty_and_single_write(self):
        worker = self.worker(lambda: self.adapter.set_setting("speed_print", "extruder", 48, "0"))
        self.app.stack.extruders["0"].write_wait = worker[3]
        self.app.callbacks.get(timeout=1)()
        self.assert_error(self.finish(worker), "qt_timeout")
        self.assertEqual(self.app.stack.extruders["0"].writes, [("speed_print", 48.0)])
        self.assertEqual(self.adapter.get_setting("speed_print", "extruder", "0")["data"]["value"], 48)
