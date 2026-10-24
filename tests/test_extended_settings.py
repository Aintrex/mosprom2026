"""TASK13: bounded discovery and new keys through actual adapter/QtBridge, fake stacks only."""
import json
import unittest
from unittest import mock

import test_print_settings as base
import test_agent_integration as agent_tests
from AIAssistant.agent.mcp import dispatch
from AIAssistant.agent.tools import ToolRegistry
from AIAssistant.slicer import settings
from AIAssistant.slicer.settings_catalog import SETTING_KEYS, SETTING_TYPES, CATALOG_BY_KEY, CATEGORIES


class ExtendedSettingsTests(unittest.TestCase):
    worker, finish = base.PrintSettingsTests.worker, base.PrintSettingsTests.finish
    pump_until_done, drain = base.PrintSettingsTests.pump_until_done, base.PrintSettingsTests.drain
    start, status, complete = base.PrintSettingsTests.start, base.PrintSettingsTests.status, base.PrintSettingsTests.complete
    assert_error = base.PrintSettingsTests.assert_error
    service, send, snapshot, results = agent_tests.AgentTests.service, agent_tests.AgentTests.send, agent_tests.AgentTests.snapshot, agent_tests.AgentTests.results

    def setUp(self):
        base.PrintSettingsTests.setUp(self)

    def add(self, key, value, stack=None, **changes):
        stack = stack or self.app.stack
        stack.props[key] = {"type": SETTING_TYPES[key], "value": value, "unit": "mm",
                            "enabled": True, "settable_globally": True, "settable_per_extruder": True,
                            "minimum_value": 0, "maximum_value": 1000,
                            "minimum_value_warning": None, "maximum_value_warning": None,
                            "options": {"off": "Off", "all": "All"} if SETTING_TYPES[key] == "enum" else {}}
        stack.props[key].update(changes)
        return stack.props[key]

    def describe(self, **filters):
        result = self.adapter.describe_settings("global", **filters)
        self.assertTrue(result["ok"], result)
        return result["data"]

    def test_catalog_safety_and_common_mcp_llm_surface(self):
        legacy = {"layer_height", "layer_height_0", "line_width", "wall_line_count", "top_bottom_thickness", "infill_sparse_density",
            "infill_pattern", "support_enable", "support_type", "support_angle", "material_print_temperature", "material_bed_temperature",
            "speed_print", "retraction_enable", "retraction_amount", "retraction_speed", "cool_fan_enabled", "cool_fan_speed", "adhesion_type"}
        self.assertEqual(len(SETTING_KEYS), 120)
        self.assertEqual(len(set(SETTING_KEYS)), 120)
        self.assertEqual(len(CATEGORIES), 10)
        self.assertTrue(legacy.issubset(SETTING_KEYS))
        self.assertEqual(SETTING_TYPES["cylindrical_round_segments"], "float")
        for key in ("printing_mode", "support_structure", "machine_start_gcode", "machine_width", "cli_command"):
            self.assertNotIn(key, SETTING_KEYS)
        schemas = {t["name"]: t["inputSchema"] for t in self.tools.list_tools()}
        self.assertEqual(schemas["get_setting"]["properties"]["key"]["enum"], list(SETTING_KEYS))
        self.assertEqual(schemas["set_setting"]["properties"]["key"]["enum"], list(SETTING_KEYS))
        self.assertEqual(schemas["describe_settings"]["properties"]["keys"]["items"]["enum"], list(SETTING_KEYS))
        rpc = dispatch({"jsonrpc": "2.0", "id": 13, "method": "tools/list"}, self.tools)
        self.assertEqual(rpc["result"]["tools"], self.tools.list_tools())

    def test_default_page_reads_only_twenty_and_all_pages_stable_unique(self):
        with mock.patch.object(settings, "metadata", wraps=settings.metadata) as read:
            first = self.describe()
            self.assertEqual(read.call_count, 20)
        self.assertEqual((first["total"], first["offset"], first["limit"], first["returned"], first["next_offset"], first["truncated"]),
                         (120, 0, 20, 20, 20, True))
        self.assertEqual(first["settings"][0]["key"], "layer_height")
        seen = []
        for offset in (0, 30, 60, 90):
            with mock.patch.object(settings, "metadata", wraps=settings.metadata) as read:
                page = self.describe(offset=offset, limit=30)
                self.assertEqual(read.call_count, 30)
            seen.extend(item["key"] for item in page["settings"])
        self.assertEqual(len(set(seen)), 120)
        self.assertEqual(seen, list(SETTING_KEYS))
        self.assertEqual(seen[-1], "cylindrical_round_segments")
        last = self.describe(offset=115, limit=10)
        self.assertEqual(last["returned"], 5)
        self.assertFalse(last["truncated"])
        self.assertIsNone(last["next_offset"])

    def test_filter_intersection_catalog_order_and_casefold_before_metadata(self):
        with mock.patch.object(settings, "metadata", wraps=settings.metadata) as read:
            result = self.describe(keys=["support_interface_pattern", "layer_height", "support_pattern"], category="support", query="PATTERN", limit=1)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(read.call_args[0][1], "support_pattern")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["next_offset"], 1)
        result = self.describe(query="INITIAL LAYER PRINTING TEMPERATURE")
        self.assertEqual(result["total"], 0)  # No rearrangement/fuzzy guess of words.
        result = self.describe(query="printing temperature initial")
        self.assertEqual([i["key"] for i in result["settings"]], ["material_print_temperature_layer_0"])

    def test_empty_search_and_page_keep_machine_and_extruder_validation(self):
        for filters in ({"query": ""}, {"query": "   "}, {"query": "no-such-setting"}, {"offset": 1000}):
            with mock.patch.object(settings, "metadata", side_effect=AssertionError("No metadata for empty page")):
                result = self.describe(**filters)
            self.assertEqual(result["settings"], [])
            self.assertEqual(result["returned"], 0)
            self.assertIsNone(result["next_offset"])
        current = self.app.stack
        self.app.stack = None
        self.assert_error(self.adapter.describe_settings("global", query=""), "no_active_machine")
        self.app.stack = current
        self.assert_error(self.adapter.describe_settings("extruder", "absent", query=""), "extruder_not_found")

    def test_invalid_filters_rejected_before_api_even_if_other_filter_empty(self):
        invalid = ({"limit": True}, {"offset": False}, {"limit": 0}, {"limit": 31}, {"offset": -1}, {"offset": 1.2},
                   {"keys": []}, {"keys": ["layer_height"] * 2}, {"keys": list(SETTING_KEYS[:21])}, {"keys": [None]},
                   {"keys": "layer_height"}, {"keys": ["support_structure"]}, {"category": "machine"}, {"category": []},
                   {"query": 1}, {"query": "x" * 81}, {"keys": None}, {"extra": True})
        with mock.patch.object(self.app, "getGlobalContainerStack", side_effect=AssertionError("No API")):
            for filters in invalid:
                args = dict({"scope": "global", "query": ""}, **filters)
                response = dispatch({"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": "describe_settings", "arguments": args}}, self.tools)
                self.assertEqual(response["error"]["code"], -32602, args)

    def test_descriptions_origin_truncation_runtime_values_and_unavailable(self):
        props = self.add("classic_layer_height", 0.17, label="Runtime label" * 30, description="Definition description " * 60)
        item = self.describe(keys=["classic_layer_height"])["settings"][0]
        self.assertEqual(item["value"], 0.17)
        self.assertEqual(item["label"], props["label"][:200])
        self.assertEqual(item["description"], props["description"][:500])
        self.assertTrue(item["description_truncated"])
        self.assertTrue(item["label_truncated"])
        self.assertEqual(item["text_source"], {"label": "definition_api", "description": "definition_api", "category": "catalog"})
        item = self.describe(keys=["support_xy_distance"])["settings"][0]
        self.assertFalse(item["available"])
        self.assertIsNone(item["value"])
        self.assertIsNone(item["description"])
        self.assertEqual(item["label"], "Classic Support X/Y Distance")
        self.assertEqual(item["text_source"]["label"], "catalog")
        self.assertEqual(item["category"], "support")

    def test_representative_new_types_groups_write_and_actual_readback(self):
        samples = (("classic_layer_height", 0.2, 0.16), ("support_z_distance", 0.2, 0.3), ("support_xy_distance", 0.7, 0.8),
                   ("brim_width", 5.0, 6.0), ("raft_surface_layers", 2, 3), ("cool_fan_full_layer", 2, 4),
                   ("retraction_hop_enabled", False, True), ("retraction_combing", "off", "all"),
                   ("cylindrical_layer_height", 0.2, 0.15), ("cylindrical_round_segments", 16.0, 16.5))
        for key, before, after in samples:
            self.add(key, before)
            result = self.adapter.set_setting(key, "global", after)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["data"]["before"], before)
            self.assertEqual(result["data"]["after"], after)
            self.assertEqual(self.adapter.get_setting(key, "global")["data"]["value"], after)
        self.assertIs(type(self.adapter.get_setting("cool_fan_full_layer", "global")["data"]["value"]), int)
        self.assertIs(type(self.adapter.get_setting("cylindrical_round_segments", "global")["data"]["value"]), float)

    def test_new_extruder_temperature_explicit_target_and_context_limits(self):
        for stack in self.app.stack.extruders.values():
            self.add("material_print_temperature_layer_0", 205, stack, unit="°C", maximum_value=300)
        result = self.adapter.set_setting("material_print_temperature_layer_0", "extruder", 215, "0")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["unit"], "°C")
        self.assertEqual(self.app.stack.extruders["0"].writes, [("material_print_temperature_layer_0", 215.0)])
        self.assertEqual(self.app.stack.extruders["1"].writes, [])
        self.assertEqual(self.app.stack.writes, [])
        self.assertEqual(self.app.ui_extruder, "1")

    def test_new_bounds_enabled_readonly_runtime_type_scope_not_bypassed(self):
        props = self.add("cylindrical_layer_height", 0.2, minimum_value=0.01, maximum_value=1, minimum_value_warning=0.1)
        self.assert_error(self.adapter.set_setting("cylindrical_layer_height", "global", 0), "setting_out_of_range")
        result = self.adapter.set_setting("cylindrical_layer_height", "global", 0.05)
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["warnings"])
        for change, code in (({"enabled": False}, "setting_disabled"), ({"type": "int"}, "setting_metadata_unavailable"),
                             ({"settable_globally": False}, "setting_scope_unavailable")):
            previous = dict(props)
            props.update(change)
            self.assert_error(self.adapter.set_setting("cylindrical_layer_height", "global", 0.2), code)
            props.clear(); props.update(previous)
        self.app.stack.readonly = True
        self.assert_error(self.adapter.set_setting("cylindrical_layer_height", "global", 0.2), "setting_read_only")
        self.assertEqual(self.app.stack.writes, [("cylindrical_layer_height", 0.05)])

    def test_new_enum_runtime_options_and_numeric_validation_not_coerced(self):
        props = self.add("retraction_combing", "off")
        self.assert_error(self.adapter.set_setting("retraction_combing", "global", "Translated All"), "setting_option_unavailable")
        props["options"]["runtime_custom"] = "Custom runtime"
        self.assertTrue(self.adapter.set_setting("retraction_combing", "global", "runtime_custom")["ok"])
        with mock.patch.object(self.app, "getGlobalContainerStack", side_effect=AssertionError("No API")):
            for key, value in (("cool_fan_full_layer", 2.5), ("raft_surface_layers", True), ("brim_width", "6"),
                               ("cylindrical_layer_height", float("nan")), ("retraction_hop_enabled", 1), ("retraction_combing", "=expression")):
                with self.assertRaises(ValueError):
                    self.adapter.set_setting(key, "global", value)

    def test_new_key_invalidates_completed_slice_restore_does_not_revive_it(self):
        self.add("support_xy_distance", 0.7)
        self.complete()
        self.assertTrue(self.adapter.get_slicing_result(self.slice_id)["ok"])
        self.assertTrue(self.adapter.set_setting("support_xy_distance", "global", 0.8)["ok"])
        self.assertFalse(self.status()["valid"])
        self.assert_error(self.adapter.get_slicing_result(self.slice_id), "slice_result_stale")
        self.assertTrue(self.adapter.set_setting("support_xy_distance", "global", 0.7)["ok"])
        self.assertFalse(self.status()["valid"])

    def test_agent_targeted_metadata_new_key_one_write_readback_same_registry(self):
        self.add("support_xy_distance", 0.7, description="Runtime support gap description")
        self.service(agent_tests.calls(agent_tests.tool("describe_settings", {"scope": "global", "keys": ["support_xy_distance"]})),
                     agent_tests.calls(agent_tests.tool("set_setting", {"key": "support_xy_distance", "scope": "global", "value": 0.8}, "write")),
                     agent_tests.calls(agent_tests.tool("get_setting", {"key": "support_xy_distance", "scope": "global"}, "readback")), {"content": "Confirmed"})
        self.pump_until_done(self.worker(self.send))
        described = self.results()[0]["data"]
        self.assertEqual((described["total"], described["returned"]), (1, 1))
        self.assertEqual(described["settings"][0]["description"], "Runtime support gap description")
        self.assertEqual(self.app.stack.writes, [("support_xy_distance", 0.8)])
        self.assertEqual(self.results()[-1]["data"]["value"], 0.8)
        self.assertEqual(len(self.llm.requests), 4)
        json.dumps(self.snapshot(), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
