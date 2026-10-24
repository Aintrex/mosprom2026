"""LLM-boundary argument repair and read-only wait tools, no STE/provider."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin"))
from AIAssistant.agent.tools import ToolRegistry
from AIAssistant.agent.service import HISTORY_TOOL_CHARS, HISTORY_TOTAL_CHARS, compact_history, llm_view


class FakeAdapter(object):
    def __init__(self, states):
        self.states, self.reads = list(states), 0

    def get_slicing_status(self, slice_id):
        self.reads += 1
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"ok": True, "data": {"slice_id": slice_id, "state": state, "progress": None, "valid": state == "completed"}}

    def get_export_status(self, export_id):
        return {"ok": False, "error": {"code": "export_not_found", "message": "нет"}}


class CoercionTests(unittest.TestCase):
    coerce = staticmethod(ToolRegistry.coerce_llm_arguments)

    def test_transform_numbers_left_for_strict_rejection(self):
        args = {"object_id": "m", "axis": "x", "angle_degrees": "90"}
        self.assertEqual(self.coerce("rotate_model", args), (args, []))

    def test_typed_settings_only(self):
        # int/float strings are left to ToolRegistry.llm_arguments (strict numeric path).
        self.assertEqual(self.coerce("set_setting", {"key": "wall_line_count", "scope": "global", "value": "3"})[0]["value"], "3")
        self.assertEqual(ToolRegistry.llm_arguments("set_setting", {"key": "wall_line_count", "scope": "global", "value": "3"})[0]["value"], 3)
        self.assertEqual(self.coerce("describe_settings", {"scope": "global", "limit": "10"})[0]["limit"], 10)
        self.assertIs(self.coerce("set_setting", {"key": "support_enable", "scope": "global", "value": "False"})[0]["value"], False)
        # enum strings and unknown keys are never touched
        self.assertEqual(self.coerce("set_setting", {"key": "adhesion_type", "scope": "global", "value": "1"})[0]["value"], "1")
        self.assertEqual(self.coerce("set_setting", {"key": "nope", "scope": "global", "value": "1"})[1], [])

    def test_rejects_non_plain_decimals(self):
        for value in ("1e999", "nan", "Infinity", "0.2 mm", "1,5", "=0.2", "", "0x10"):
            args, notes = self.coerce("set_setting", {"key": "layer_height", "scope": "global", "value": value})
            self.assertEqual((args["value"], notes), (value, []))

    def test_extruder_scope_keeps_null_for_strict_rejection(self):
        args, notes = self.coerce("set_setting", {"key": "layer_height", "scope": "extruder", "value": 0.1, "extruder_id": None})
        self.assertIn("extruder_id", args)
        self.assertEqual(notes, [])


class PayloadTests(unittest.TestCase):
    def test_llm_view_rounds_and_compacts_materials(self):
        result = {"ok": True, "data": {"bounds": {"x": 109.84250994460308}, "count": 3,
                  "materials": [{"name": "PETG", "root_material_id": "generic_petg_175", "compatible": True,
                                 "brand": "Generic", "material": "PETG", "material_id": "generic_petg_175"}]}}
        view = json.loads(llm_view("list_materials", result))
        self.assertEqual(view["data"]["bounds"]["x"], 109.843)
        self.assertEqual(view["data"]["count"], 3)
        self.assertEqual(view["data"]["materials"], [{"name": "PETG", "root_material_id": "generic_petg_175", "compatible": True}])
        self.assertEqual(result["data"]["materials"][0]["brand"], "Generic", "UI copy is untouched")

    def test_history_shortens_old_tool_output_and_drops_oldest_turns(self):
        def turn(i, size):
            return [{"role": "user", "content": "q%d" % i},
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "c%d" % i}]},
                    {"role": "tool", "tool_call_id": "c%d" % i, "content": "x" * size},
                    {"role": "assistant", "content": "a%d" % i}]
        blocks = [turn(i, 5000) for i in range(80)]
        kept = compact_history(blocks)
        self.assertLess(len(kept), 80)
        self.assertEqual(kept[-1][0]["content"], "q79", "Newest turn is always kept")
        self.assertTrue(all(len(b[2]["content"]) <= HISTORY_TOOL_CHARS + 60 for b in kept))
        self.assertTrue(all(b[2]["tool_call_id"] == "c" + b[0]["content"][1:] for b in kept), "Tool pairs stay intact")
        self.assertLessEqual(sum(len(e["content"] or "") for b in kept for e in b), HISTORY_TOTAL_CHARS + 1000)
        self.assertEqual(len(blocks[0][2]["content"]), 5000, "Stored history is not modified")


class WaitTests(unittest.TestCase):
    def test_wait_until_completed_polls_status(self):
        adapter = FakeAdapter(["queued", "running", "completed"])
        registry = ToolRegistry(adapter)
        now = [0.0]
        result = registry._wait("wait_for_slicing", {"slice_id": "s"}, clock=lambda: now[0],
                                sleep=lambda s: now.__setitem__(0, now[0] + s))
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["state"], "completed")
        self.assertTrue(result["data"]["wait_finished"])
        self.assertEqual(adapter.reads, 3)

    def test_wait_timeout_reports_unfinished(self):
        registry = ToolRegistry(FakeAdapter(["running"]))
        now = [0.0]
        result = registry._wait("wait_for_slicing", {"slice_id": "s", "max_seconds": 3}, clock=lambda: now[0],
                                sleep=lambda s: now.__setitem__(0, now[0] + s))
        self.assertEqual(result["data"]["state"], "running")
        self.assertFalse(result["data"]["wait_finished"])

    def test_wait_error_returned_and_validation(self):
        registry = ToolRegistry(FakeAdapter(["running"]))
        self.assertFalse(registry.call("wait_for_export", {"export_id": "e"})["ok"])
        for bad in ({}, {"export_id": ""}, {"export_id": "e", "max_seconds": 0}, {"export_id": "e", "max_seconds": True},
                    {"export_id": "e", "extra": 1}):
            with self.assertRaises(ValueError):
                registry.validate("wait_for_export", bad)
        self.assertFalse(registry.is_mutating("wait_for_slicing"))


if __name__ == "__main__":
    unittest.main()
