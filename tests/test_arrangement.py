"""TASK 09: correlated fork-shaped native job, no packing engine/Qt acceptance."""
import copy
import json
import sys
import threading
import types
import unittest
from unittest import mock

import test_model_transforms as transform_tests
import test_agent_integration as agent_tests
import test_slicer_slicing as slicing_tests
from AIAssistant.agent.mcp import dispatch
from AIAssistant.agent.service import AgentService
from AIAssistant.agent.tools import ToolRegistry
from AIAssistant.slicer.adapter import SlicerAdapter


class Hull(object):
    def __init__(self, points=None):
        self.points = points if points is not None else [[0, 0], [2, 0], [2, 4], [0, 4]]
    def getPoints(self):
        return self.points


class Node(transform_tests.Node):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.hull, self.head, self.selectable = Hull(), None, True
    def isSelectable(self):
        self.app.check_thread()
        return self.selectable
    def callDecoration(self, name):
        self.app.check_thread()
        if name == "getConvexHull":
            return self.hull
        if name == "getConvexHullHead":
            return self.head
        return super().callDecoration(name)


class JobSignal(slicing_tests.Signal):
    def emit(self, *args):
        # UM.Auto can emit on JobQueue's worker; wrapper must schedule own Qt reads.
        for callback in list(self.slots):
            callback(*args)


class Job(object):
    app = None
    created = []
    failure = None
    def __init__(self, nodes, fixed_nodes, min_offset=8):
        self.app.check_thread()
        self.nodes, self.fixed, self.offset = nodes, fixed_nodes, min_offset
        self.finished = JobSignal(self.app)
        self.running, self.done, self.error, self.starts = False, False, None, 0
        Job.created.append(self)
    def start(self):
        self.app.check_thread()
        self.starts += 1
        if self.failure:
            raise self.failure
    def isRunning(self):
        self.app.check_thread()
        return self.running
    def isFinished(self):
        self.app.check_thread()
        return self.done
    def hasError(self):
        self.app.check_thread()
        return self.error is not None
    def getError(self):
        self.app.check_thread()
        return self.error
    def getResult(self):
        raise AssertionError("Native arrange result None is not a completion criterion")
    def begin(self):
        self.running = True
    def place(self, outside=False):
        # Fake native effect. Tests do not implement or claim a packing algorithm.
        from AIAssistant.slicer.models import bounds
        for i, node in enumerate(self.nodes):
            y = node.getWorldPosition().y - bounds(node)["min"]["y"]
            node.setPosition(transform_tests.Vector(200 if outside and i == 0 else -10 + i * 20, y, 10))
    def early(self):
        self.finished.emit(self)
    def finish(self):
        self.running, self.done = False, True
        self.finished.emit(self)


class ArrangementTests(unittest.TestCase):
    worker = slicing_tests.SlicingTests.worker
    finish = slicing_tests.SlicingTests.finish
    pump_until_done = slicing_tests.SlicingTests.pump_until_done
    drain = slicing_tests.SlicingTests.drain
    assert_error = slicing_tests.SlicingTests.assert_error
    complete = slicing_tests.SlicingTests.complete
    start = slicing_tests.SlicingTests.start
    status = slicing_tests.SlicingTests.status

    def setUp(self):
        transform_tests.TransformTests.setUp(self)
        self.app.scene.root = Node(self.app, "root", mesh=False, sliceable=False)
        self.node = self.app.scene.root.add(Node(self.app))
        self.other = self.app.scene.root.add(Node(self.app))
        self.foreign = self.app.scene.root.add(Node(self.app))
        self.foreign.plate = 1
        self.volume.getBoundingBox = lambda: types.SimpleNamespace(width=200, depth=200)
        self.volume.getEdgeDisallowedSize = lambda: 7
        Job.app, Job.created, Job.failure = self.app, [], None
        module = types.ModuleType("steslicer.Arranging.ArrangeObjectsJob")
        module.ArrangeObjectsJob = Job
        patch = mock.patch.dict(sys.modules, {module.__name__: module})
        patch.start()
        self.addCleanup(patch.stop)
        self.ids = [m["object_id"] for m in self.adapter.list_models()["data"]["models"]]

    def arrange(self):
        result = self.tools.call("arrange_models", {})
        self.assertTrue(result["ok"], result)
        self.arrangement_id = result["data"]["arrangement_id"]
        self.job = Job.created[-1]
        return result["data"]

    def snapshot(self):
        result = self.adapter.get_arrangement_status(self.arrangement_id)
        self.assertTrue(result["ok"], result)
        return result["data"]

    def test_validation_dev_unknown_id_schemas_and_mutation_classification(self):
        self.assertTrue(self.tools.is_mutating("arrange_models"))
        self.assertFalse(self.tools.is_mutating("get_arrangement_status"))
        schemas = {t["name"]: t["inputSchema"] for t in self.tools.list_tools()}
        self.assertEqual(schemas["arrange_models"]["required"], [])
        self.assertFalse(schemas["get_arrangement_status"]["additionalProperties"])
        self.assert_error(SlicerAdapter().arrange_models(), "slicer_not_connected")
        self.assert_error(SlicerAdapter().get_arrangement_status("id"), "slicer_not_connected")
        self.assert_error(self.adapter.get_arrangement_status("missing"), "arrangement_not_found")
        with mock.patch.object(self.app, "getController", side_effect=AssertionError("No API")):
            for name, args in (("arrange_models", {"extra": 1}), ("arrange_models", []),
                               ("get_arrangement_status", {}), ("get_arrangement_status", {"arrangement_id": ""}),
                               ("get_arrangement_status", {"arrangement_id": 1}),
                               ("get_arrangement_status", {"arrangement_id": "id", "extra": 1})):
                response = dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                    "name": name, "arguments": args}}, self.tools)
                self.assertEqual(response["error"]["code"], -32602)
        self.assertEqual(Job.created, [])

    def test_worker_start_queued_running_double_finished_actual_after_and_other_plate(self):
        before = self.adapter.list_models()["data"]
        foreign = copy.deepcopy(self.foreign.matrix)
        worker = self.worker(lambda: self.tools.call("arrange_models", {}))
        callback = self.app.callbacks.get(timeout=1)
        self.assertEqual(Job.created, [])
        callback()
        result = self.finish(worker)
        self.assertTrue(result["ok"], result)
        self.arrangement_id = result["data"]["arrangement_id"]
        self.job = Job.created[-1]
        self.assertEqual(result["data"]["state"], "queued")
        self.assertIsNone(result["data"]["progress"])
        self.assertIsNone(result["data"]["all_fit"])
        self.assertEqual(self.job.offset, 9)
        self.assertEqual(self.job.fixed, [])
        self.assertEqual(self.job.nodes, [self.node, self.other])
        self.job.begin()
        self.assertEqual(self.snapshot()["state"], "running")
        self.job.place()
        self.job.early()
        self.drain()
        self.assertEqual(self.snapshot()["state"], "running")
        self.assertTrue(self.adapter._change_lock.locked())
        callback = self.job.finished.slots[0]
        # Emit final from worker: job flags must not be read until Qt processes callLater.
        worker = self.worker(self.job.finish)
        self.finish(worker)
        self.assertTrue(self.adapter._change_lock.locked())
        self.drain()
        completed = self.snapshot()
        self.assertEqual(completed["state"], "completed")
        self.assertIsNone(completed["all_fit"])
        self.assertTrue(completed["valid"])
        self.assertIsNone(completed["progress"])
        self.assertEqual(completed["models"][0]["after"]["position_mm"], {"x": -10, "y": 0, "z": 10})
        self.assertEqual(completed["models"][0]["before"]["position_mm"], {"x": 0, "y": 0, "z": 0})
        self.assertEqual(self.foreign.matrix, foreign)
        self.assertEqual([m["object_id"] for m in self.adapter.list_models()["data"]["models"]], [m["object_id"] for m in before["models"]])
        self.assertFalse(self.adapter._change_lock.locked())
        callback(self.job)
        self.drain()
        self.assertEqual(self.snapshot(), completed)
        self.assertEqual(self.job.finished.slots, [])
        json.dumps(completed, allow_nan=False)

    def test_partial_outside_and_unknown_native_boundary_are_not_arranged_true(self):
        self.arrange()
        self.job.begin()
        self.job.place(outside=True)
        self.job.finish()
        self.drain()
        result = self.snapshot()
        self.assertEqual(result["state"], "completed")
        self.assertFalse(result["all_fit"])
        self.assertTrue(result["models"][0]["after"]["outside_build_area"])
        self.arrange()
        self.job.place()
        self.volume.getBoundingBox = lambda: None
        self.job.finish()
        self.drain()
        self.assertIsNone(self.snapshot()["all_fit"])

    def test_no_observed_movement_is_completed_but_application_not_confirmed(self):
        self.arrange()
        self.job.finish()
        self.drain()
        result = self.snapshot()
        self.assertEqual(result["state"], "completed")
        self.assertIsNone(result["all_fit"])
        self.assertTrue(result["warnings"])
        self.node.setPosition(transform_tests.Vector(200, 0, 0))
        self.arrange()
        self.job.finish()  # Unchanged position still has a confirmed outside=True.
        self.drain()
        result = self.snapshot()
        self.assertFalse(result["all_fit"])
        self.assertTrue(result["models"][0]["after"]["outside_build_area"])

    def test_native_fallback_x200_inside_wide_table_is_not_full_solution(self):
        self.volume.getBoundingBox = lambda: types.SimpleNamespace(width=600, depth=600)
        self.volume.checkBoundsAndUpdate = lambda node: setattr(node, "outside", False)
        self.arrange()
        self.job.place(outside=True)  # Native fallback X=200 fits this table's boundary.
        self.job.finish()
        self.drain()
        result = self.snapshot()
        self.assertEqual(result["state"], "completed")
        self.assertFalse(result["models"][0]["after"]["outside_build_area"])
        self.assertEqual(result["models"][0]["after"]["position_mm"]["x"], 200)
        self.assertIsNone(result["all_fit"])

    def test_empty_other_platform_mode_machine_auto_physics_preview_parent_and_hull_preflight(self):
        def denied(code):
            self.assert_error(self.adapter.arrange_models(), code)
            self.assertEqual(Job.created, [])
            self.assertFalse(self.adapter._change_lock.locked())
        stack = self.app.stack
        self.app.stack = None
        denied("no_active_machine")
        self.app.stack = stack
        self.app.stack.mode = "cylindrical"
        denied("unsupported_arrangement_mode")
        self.app.stack.mode = "classic"
        self.app.auto_slice = True
        denied("auto_slicing_enabled")
        self.app.auto_slice = False
        self.physics["physics/automatic_drop_down"] = True
        denied("arrangement_unsupported")
        self.physics["physics/automatic_drop_down"] = False
        self.node.plate = self.other.plate = 1
        denied("empty_scene")
        self.node.plate = self.other.plate = 0
        service = self.app.scene.root.add(Node(self.app, preview=True))
        denied("arrangement_unsupported")
        self.app.scene.root.children.remove(service)
        self.app.scene.root.matrix[0][3] = 1
        denied("arrangement_unsupported")
        self.app.scene.root.matrix[0][3] = 0
        self.node.selectable = False
        denied("arrangement_unsupported")
        self.node.selectable = True
        for hull in (None, Hull([]), Hull([[0, 0]] * 3), Hull([[float("nan"), 0]] * 3)):
            self.node.hull = hull
            denied("arrangement_hull_unavailable")
        self.node.hull = Hull()
        self.node.matrix[0][0] = 200
        denied("arrangement_unsupported")  # Too large is rejected, not silently skipped.

    def test_group_all_parts_platform_and_actual_relative_geometry_preserved(self):
        group = self.app.scene.root.add(Node(self.app, group=True, mesh=False, sliceable=False))
        child = group.add(Node(self.app))
        child.matrix[0][3], child.matrix[1][3] = 3, 5
        child.plate = 1
        self.assert_error(self.adapter.arrange_models(), "arrangement_unsupported")
        child.plate = 0
        self.arrange()
        self.job.place()
        self.job.finish()
        self.drain()
        group_result = self.snapshot()["models"][-1]
        self.assertEqual(group_result["after"]["bounds"]["min"]["y"], 0)
        self.assertEqual(group_result["after"]["position_mm"]["y"], -5)
        self.assertEqual(child.matrix[0][3], 3)
        self.assertEqual(self.snapshot()["state"], "completed")

    def test_own_job_only_foreign_signal_no_early_release_or_result_none_dependency(self):
        self.arrange()
        self.job.finished.emit(object())
        self.drain()
        self.assertEqual(self.snapshot()["state"], "queued")
        self.assertTrue(self.adapter._change_lock.locked())
        self.job.begin()
        self.job.early()
        self.drain()
        self.assertEqual(self.snapshot()["state"], "running")
        self.job.place()
        self.job.finish()
        self.drain()
        self.assertEqual(self.snapshot()["state"], "completed")

    def test_native_error_partial_after_private_error_not_leaked(self):
        self.arrange()
        self.job.place()
        self.job.error = ValueError("private path/provider value")
        with self.assertLogs("AIAssistant.slicer.arrangement", level="ERROR") as captured:
            self.job.finish()
            self.drain()
        result = self.snapshot()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"]["code"], "arrangement_failed")
        self.assertIsNotNone(result["models"][0]["after"])
        self.assertNotIn("private path", json.dumps(result) + "".join(captured.output))
        self.assertFalse(self.adapter._change_lock.locked())

    def test_guard_across_old_mutations_and_status_read_available(self):
        self.arrange()
        self.assert_error(self.adapter.arrange_models(), "arrangement_busy")
        self.assert_error(self.adapter.delete_model(self.ids[0]), "model_operation_busy")
        self.assert_error(self.adapter.start_slicing(), "slicing_busy")
        self.assert_error(self.adapter.export_gcode("missing"), "export_busy")
        self.assert_error(self.adapter.set_setting("layer_height", "global", 0.24), "slicing_busy")
        self.assertTrue(self.adapter.get_model_state(self.ids[0])["ok"])
        self.assertEqual(self.snapshot()["state"], "queued")

    def test_timeout_late_finish_retains_failure_and_guard_until_exit(self):
        now = [0.0]
        self.adapter._arrangement.clock = lambda: now[0]
        self.arrange()
        self.job.begin()
        now[0] = 121
        status = self.snapshot()
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["error"]["code"], "arrangement_timeout")
        self.assertTrue(self.adapter._change_lock.locked())
        self.job.early()
        self.drain()
        self.assertTrue(self.adapter._change_lock.locked())
        self.job.place()
        self.job.finish()
        self.drain()
        self.assertEqual(self.snapshot()["state"], "failed")
        self.assertIsNotNone(self.snapshot()["models"][0]["after"])
        self.assertFalse(self.adapter._change_lock.locked())

    def test_context_geometry_foreign_platform_and_relative_group_edits_fail(self):
        changes = (lambda: setattr(self.app.plates, "activeBuildPlate", 1),
                   lambda: self.node.matrix[0].__setitem__(0, 2),
                   lambda: self.foreign.setPosition(transform_tests.Vector(20, 0, 20)),
                   lambda: self.app.scene.root.children.remove(self.other))
        for change in changes:
            with self.subTest(change=change):
                # Restore a fresh test scene/manager after each failed job.
                self.app.plates.activeBuildPlate = 0
                if self.other not in self.app.scene.root.children:
                    self.app.scene.root.add(self.other)
                self.arrange()
                self.job.place()
                change()
                self.job.finish()
                self.drain()
                self.assertEqual(self.snapshot()["state"], "failed")
                self.assertFalse(self.adapter._change_lock.locked())

    def test_completed_snapshot_becomes_stale_after_scene_edit(self):
        self.arrange()
        self.job.place()
        self.job.finish()
        self.drain()
        self.assertTrue(self.snapshot()["valid"])
        self.node.setPosition(transform_tests.Vector(40, 0, 0))
        result = self.snapshot()
        self.assertEqual(result["state"], "completed")
        self.assertFalse(result["valid"])
        self.assertEqual(result["models"][0]["after"]["position_mm"]["x"], -10)

    def test_old_snapshot_is_invalidated_before_new_job_without_live_geometry_reads(self):
        self.arrange()
        self.job.place()
        self.job.finish()
        self.drain()
        previous_id = self.arrangement_id
        self.assertTrue(self.snapshot()["valid"])
        self.arrange()
        self.job.begin()
        with mock.patch.object(self.adapter._models, "nodes", side_effect=AssertionError("No mid-job geometry check")):
            old = self.adapter.get_arrangement_status(previous_id)
        self.assertTrue(old["ok"], old)
        self.assertFalse(old["data"]["valid"])

    def test_shutdown_live_job_retains_context_until_finished_and_late_callbacks_no_mutation(self):
        self.arrange()
        self.job.begin()
        record = self.adapter._arrangement.active
        self.adapter.close()
        self.assertTrue(self.adapter._change_lock.locked())
        self.assertIs(record.job, self.job)
        self.job.early()
        self.drain()
        self.assertTrue(self.adapter._change_lock.locked())
        self.job.finish()
        self.drain()
        self.assertFalse(self.adapter._change_lock.locked())
        self.assertIsNone(record.job)
        self.assertIsNone(record.targets)
        self.assertEqual(record.state, "failed")
        self.assert_error(self.adapter.arrange_models(), "slicer_not_connected")

    def test_pending_start_timeout_no_job_and_uncertain_start_retains_id_and_guard(self):
        worker = self.worker(self.adapter.arrange_models)
        late = self.app.callbacks.get(timeout=1)
        self.assert_error(self.finish(worker), "qt_timeout")
        late()
        self.assertEqual(Job.created, [])
        Job.failure = RuntimeError("start private value")
        with self.assertLogs("AIAssistant.slicer.arrangement", level="ERROR"):
            result = self.arrange()
        self.assertEqual(result["state"], "failed")
        self.assertTrue(self.adapter._change_lock.locked())
        self.job.finish()
        self.drain()
        self.assertFalse(self.adapter._change_lock.locked())

    def test_preflight_exceeding_qt_budget_does_not_start_late_native_job(self):
        worker = self.worker(self.adapter.arrange_models)
        callback = self.app.callbacks.get(timeout=1)
        original = self.node.hull.getPoints
        def delayed_hull():
            self.assertTrue(worker[3].wait(2))
            return original()
        with mock.patch.object(self.node.hull, "getPoints", side_effect=delayed_hull):
            callback()
        self.assert_error(self.finish(worker), "qt_timeout")
        self.assertEqual(Job.created, [])
        self.assertFalse(self.adapter._change_lock.locked())

    def test_slice_stale_and_agent_registry_stops_after_arrange_error(self):
        self.foreign.plate = 0  # Slicing prototype requires a single nonempty plate.
        self.complete()
        self.arrange()
        self.assert_error(self.adapter.get_slicing_result(self.slice_id), "slice_result_stale")
        llm = agent_tests.ScriptedLLM(agent_tests.calls(agent_tests.tool("arrange_models"),
            agent_tests.tool("delete_model", {"object_id": self.ids[0]}, "delete")))
        service = AgentService(self.adapter, lambda: dict(agent_tests.CONFIG), llm, run_async=False)
        self.addCleanup(service.close)
        sid = service.create_session()["session_id"]
        service.submit_message(sid, {"text": "Расставь модели", "client_message_id": "arrange"})
        results = [e["data"]["result"] for e in service.get_session(sid)["tool_events"] if e["type"] == "tool_finished"]
        self.assertEqual([r["error"]["code"] for r in results], ["arrangement_busy", "tool_not_executed"])
        self.assertEqual(len(llm.requests), 1)
