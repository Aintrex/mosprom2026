"""TASK 08: fork-shaped UM operations/scene on a Qt queue, no native acceptance."""
import copy
import json
import math
import os
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

import test_slicer_models as model_tests
import test_slicer_slicing as slicing_tests
import test_agent_integration as agent_tests
from AIAssistant.agent.mcp import dispatch
from AIAssistant.agent.service import AgentService
from AIAssistant.agent.tools import ToolRegistry
from AIAssistant.slicer.adapter import SlicerAdapter


def product(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


class Vector(object):
    def __init__(self, x=0, y=0, z=0):
        self.x, self.y, self.z = x, y, z
    def __neg__(self):
        return Vector(-self.x, -self.y, -self.z)


class Quaternion(object):
    @staticmethod
    def fromAngleAxis(angle, axis):
        result = Quaternion()
        c, s, result.value = math.cos(angle), math.sin(angle), model_tests.identity()
        i = next(i for i, a in enumerate((axis.x, axis.y, axis.z)) if a == 1)
        j, k = (i + 1) % 3, (i + 2) % 3
        result.value[j][j] = result.value[k][k] = c
        result.value[j][k], result.value[k][j] = -s, s
        return result
    def toMatrix(self):
        return slicing_tests.Matrix(self.value)


class Node(slicing_tests.Node):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enabled, self.outside = True, False
    def getParent(self):
        self.app.check_thread()
        return self.parent
    def isEnabled(self):
        self.app.check_thread()
        return self.enabled
    def getPosition(self):
        self.app.check_thread()
        return Vector(*[self.matrix[i][3] for i in range(3)])
    def getWorldPosition(self):
        self.app.check_thread()
        return Vector(*[self.getWorldTransformation()[i][3] for i in range(3)])
    def getLocalTransformation(self):
        self.app.check_thread()
        return copy.deepcopy(self.matrix)
    def setTransformation(self, value):
        self.app.check_thread()
        self.matrix = copy.deepcopy(value)
        self.app.scene.sceneChanged.emit(self)
    def setPosition(self, value, space=None):
        self.app.check_thread()
        for i, v in enumerate((value.x, value.y, value.z)):
            self.matrix[i][3] = v
        self.app.scene.sceneChanged.emit(self)
    def rotate(self, rotation, space=None):
        self.setTransformation(product(rotation.toMatrix(), self.matrix))
    def scale(self, factors, space=None):
        value = model_tests.identity()
        for i, v in enumerate((factors.x, factors.y, factors.z)):
            value[i][i] = v
        self.setTransformation(product(value, self.matrix))
    def setParent(self, parent):
        self.app.check_thread()
        if self.parent:
            self.parent.children.remove(self)
        self.parent = parent
        if parent:
            parent.children.append(self)
        self.app.scene.sceneChanged.emit(self)
    def isOutsideBuildArea(self):
        self.app.check_thread()
        return self.outside


class Operation(object):
    def __init__(self, node):
        self.node, self.old = node, node.getLocalTransformation()
    def undo(self):
        self.node.setTransformation(self.old)


class TranslateOperation(Operation):
    def __init__(self, node, translation, set_position=False):
        super().__init__(node)
        self.translation, self.absolute = translation, set_position
    def redo(self):
        if not self.absolute:
            raise AssertionError("Absolute position required")
        self.node.setPosition(self.translation, "world")


class RotateOperation(Operation):
    def __init__(self, node, rotation, rotate_around_point=None):
        super().__init__(node)
        self.rotation, self.pivot = rotation, rotate_around_point
    def redo(self):
        self.node.setPosition(-self.pivot)
        self.node.rotate(self.rotation, "world")
        self.node.setPosition(self.pivot)


class ScaleOperation(Operation):
    def __init__(self, node, scale, set_scale=False, add_scale=False, relative_scale=False, scale_around_point=None):
        super().__init__(node)
        if set_scale or add_scale or relative_scale:
            raise AssertionError("Multiplicative world scaling required")
        self.scale, self.pivot = scale, scale_around_point
    def redo(self):
        self.node.setPosition(-self.pivot, "world")
        self.node.scale(self.scale, "world")
        self.node.setPosition(self.pivot, "world")


class RemoveSceneNodeOperation(Operation):
    def __init__(self, node):
        super().__init__(node)
        self.parent = node.getParent()
    def redo(self):
        self.node.setParent(None)
        if self.node in self.node.app.selected:
            self.node.app.selected.remove(self.node)
    def undo(self):
        self.node.setParent(self.parent)


class OperationStack(object):
    def __init__(self, app):
        self.app, self.operations = app, []
        self.ignore, self.after_redo = False, None
    def push(self, operation):
        self.app.check_thread()
        if self.ignore:
            return
        self.operations.append(operation)
        operation.redo()
        if self.after_redo:
            self.after_redo()
    def undo(self):
        self.app.check_thread()
        self.operations.pop().undo()


class Volume(object):
    def __init__(self, app):
        self.app, self.available, self.checked = app, True, []
    def getBoundingBox(self):
        self.app.check_thread()
        return object() if self.available else None
    def checkBoundsAndUpdate(self, node):
        self.app.check_thread()
        self.checked.append(node)
        from AIAssistant.slicer.models import bounds
        box = bounds(node)
        node.outside = bool(box and any(box["min"][a] < -100 or box["max"][a] > 100 for a in ("x", "y", "z")))


class TransformTests(unittest.TestCase):
    worker = slicing_tests.SlicingTests.worker
    finish = slicing_tests.SlicingTests.finish
    pump_until_done = slicing_tests.SlicingTests.pump_until_done
    drain = slicing_tests.SlicingTests.drain
    assert_error = slicing_tests.SlicingTests.assert_error
    complete = slicing_tests.SlicingTests.complete
    start = slicing_tests.SlicingTests.start
    status = slicing_tests.SlicingTests.status

    def setUp(self):
        slicing_tests.SlicingTests.setUp(self)
        self.app.scene.root = Node(self.app, "root", mesh=False, sliceable=False)
        self.node = self.app.scene.root.add(Node(self.app))
        self.other = self.app.scene.root.add(Node(self.app))  # Identical names, distinct IDs.
        self.ops, self.volume = OperationStack(self.app), Volume(self.app)
        self.app.getOperationStack = lambda: self.ops
        self.app.getBuildVolume = lambda: self.volume
        self.app.getOutputDeviceManager = lambda: types.SimpleNamespace(getOutputDevice=lambda name: self.device)
        self.device = types.SimpleNamespace(_writing=False)
        self.app.selected = [self.node]
        self.physics = {"physics/automatic_drop_down": False, "physics/automatic_push_free": False}
        prior = self.app.getValue
        self.app.getValue = lambda name: self.physics[name] if name in self.physics else prior(name)
        modules = {}
        for path, value in (("UM.Math.Vector", Vector), ("UM.Math.Quaternion", Quaternion),
                            ("UM.Operations.TranslateOperation", TranslateOperation), ("UM.Operations.RotateOperation", RotateOperation),
                            ("UM.Operations.ScaleOperation", ScaleOperation), ("UM.Operations.RemoveSceneNodeOperation", RemoveSceneNodeOperation)):
            module = types.ModuleType(path)
            setattr(module, path.split(".")[-1], value)
            modules[path] = module
        patch = mock.patch.dict(sys.modules, modules)
        patch.start()
        self.addCleanup(patch.stop)
        self.object_id = self.adapter.list_models()["data"]["models"][0]["object_id"]

    def state(self):
        response = self.adapter.get_model_state(self.object_id)
        self.assertTrue(response["ok"], response)
        return response["data"]

    def rpc(self, name, arguments):
        return dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": name, "arguments": arguments}}, self.tools)

    def test_validation_nested_unknown_fields_types_limits_before_api(self):
        cases = []
        for name, valid in (("get_model_state", {"object_id": self.object_id}), ("delete_model", {"object_id": self.object_id}),
                            ("move_model", {"object_id": self.object_id, "position_mm": {"x": 0, "y": 1, "z": 0}}),
                            ("scale_model", {"object_id": self.object_id, "factors": {"x": 1, "y": 2, "z": 1}}),
                            ("rotate_model", {"object_id": self.object_id, "axis": "y", "angle_degrees": 90})):
            for invalid in ({}, [], dict(valid, extra=1), dict(valid, object_id=""), dict(valid, object_id=1)):
                cases.append((name, invalid))
        for value in (True, "1", None, float("nan"), float("inf"), 10 ** 1000):
            cases += [("move_model", {"object_id": self.object_id, "position_mm": {"x": value, "y": 0, "z": 0}}),
                      ("scale_model", {"object_id": self.object_id, "factors": {"x": 1, "y": value, "z": 1}}),
                      ("rotate_model", {"object_id": self.object_id, "axis": "z", "angle_degrees": value})]
        for field, value in (("axis", []), ("axis", "Y"), ("axis", 1), ("angle_degrees", 1000001)):
            args = {"object_id": self.object_id, "axis": "y", "angle_degrees": 90}
            args[field] = value
            cases.append(("rotate_model", args))
        for value in ({"x": 0, "y": 0}, {"x": 0, "y": 0, "z": 0, "extra": 1}, []):
            cases.append(("move_model", {"object_id": self.object_id, "position_mm": value}))
        for value in (0, -1, 0.009, 101):
            cases.append(("scale_model", {"object_id": self.object_id, "factors": {"x": value, "y": 1, "z": 1}}))
        cases.append(("move_model", {"object_id": self.object_id, "position_mm": {"x": 1000001, "y": 0, "z": 0}}))
        with mock.patch.object(self.app, "getController", side_effect=AssertionError("No API")):
            for name, args in cases:
                with self.subTest(name=name, args=args):
                    self.assertEqual(self.rpc(name, args)["error"]["code"], -32602)
        self.assertEqual(self.ops.operations, [])

    def test_dev_unknown_ids_and_read_state_schema(self):
        schemas = {t["name"]: t["inputSchema"] for t in self.tools.list_tools()}
        for name in ("get_model_state", "move_model", "rotate_model", "scale_model", "delete_model"):
            self.assertFalse(schemas[name]["additionalProperties"])
            self.assertEqual(ToolRegistry.is_mutating(name), name != "get_model_state")
        self.assert_error(SlicerAdapter().get_model_state("model-1"), "slicer_not_connected")
        self.assert_error(SlicerAdapter().move_model("model-1", {"x": 0, "y": 0, "z": 0}), "slicer_not_connected")
        self.assert_error(self.adapter.get_model_state("unknown"), "model_not_found")
        self.assert_error(self.adapter.delete_model("unknown"), "model_not_found")
        state = self.state()
        self.assertEqual(state["position_mm"], {"x": 0, "y": 0, "z": 0})
        self.assertEqual(state["world_transform"], model_tests.identity())
        self.assertEqual(state["bounds"]["size"], {"x": 2, "y": 3, "z": 4})
        self.assertFalse(state["outside_build_area"])
        self.assertNotIn("path", state)
        json.dumps(state, allow_nan=False)

    def test_worker_absolute_move_actual_before_after_same_name_other_untouched_and_undo(self):
        baseline, other = self.state(), copy.deepcopy(self.other.matrix)
        worker = self.worker(lambda: self.tools.call("move_model", {"object_id": self.object_id, "position_mm": {"x": 10, "y": 20, "z": 30}}))
        callback = self.app.callbacks.get(timeout=1)
        self.assertEqual(self.ops.operations, [])
        callback()
        response = self.finish(worker)
        self.assertTrue(response["ok"], response)
        result = response["data"]
        self.assertEqual(result["before"], baseline)
        self.assertEqual(result["after"], self.state())
        self.assertEqual(result["after"]["bounds"]["min"], {"x": 10, "y": 20, "z": 30})
        self.assertEqual(result["after"]["bounds"]["max"], {"x": 12, "y": 23, "z": 34})
        self.assertEqual(self.other.matrix, other)
        self.ops.undo()
        self.assertEqual(self.state(), baseline)

    def test_world_rotation_90_and_multiplicative_scale_preserve_origin_known_vertices(self):
        self.assertTrue(self.adapter.move_model(self.object_id, {"x": 10, "y": 20, "z": 30})["ok"])
        response = self.adapter.rotate_model(self.object_id, "y", 90)
        self.assertTrue(response["ok"], response)
        after = response["data"]["after"]
        self.assertEqual(after["position_mm"], {"x": 10, "y": 20, "z": 30})
        self.assertAlmostEqual(after["world_transform"][0][2], 1)
        self.assertAlmostEqual(after["world_transform"][2][0], -1)
        self.assertEqual(after["bounds"]["size"], {"x": 4, "y": 3, "z": 2})
        response = self.adapter.scale_model(self.object_id, {"x": 2, "y": 3, "z": 4})
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["data"]["after"]["position_mm"], {"x": 10, "y": 20, "z": 30})
        self.assertEqual(response["data"]["after"]["bounds"]["size"], {"x": 8, "y": 9, "z": 8})
        self.assertTrue(self.adapter.scale_model(self.object_id, {"x": 2, "y": 1, "z": 1})["ok"])
        self.assertEqual(self.state()["bounds"]["size"]["x"], 16)
        for unused in range(4):
            self.ops.undo()
        self.assertEqual(self.state()["world_transform"], model_tests.identity())

    def test_group_transforms_and_delete_whole_group_not_individual_children(self):
        group = self.app.scene.root.add(Node(self.app, mesh=False, group=True, sliceable=False))
        child = group.add(Node(self.app))
        child.matrix[0][3] = 5
        self.object_id = self.adapter.list_models()["data"]["models"][-1]["object_id"]
        response = self.adapter.rotate_model(self.object_id, "z", 90)
        self.assertTrue(response["ok"], response)
        self.assertAlmostEqual(response["data"]["after"]["bounds"]["min"]["y"], 5)
        self.assertNotIn(child, [ref() for ref in self.adapter._models._ids.values()])
        child.plate = 1
        self.assert_error(self.adapter.delete_model(self.object_id), "model_transform_unsupported")
        child.plate = 0
        self.assertTrue(self.adapter.delete_model(self.object_id)["ok"])
        self.assertIsNone(group.parent)
        self.assertIs(child.parent, group)
        self.assertEqual(len(self.adapter.list_models()["data"]["models"]), 2)
        self.ops.undo()
        self.assertEqual(len(self.adapter.list_models()["data"]["models"]), 3)

    def test_delete_membership_selection_source_file_and_new_id_after_undo(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "part.stl")
            with open(path, "wb") as stream:
                stream.write(b"source remains")
            self.node.name = path
            response = self.adapter.delete_model(self.object_id)
            self.assertTrue(response["ok"], response)
            self.assertEqual(response["data"]["removed_object_id"], self.object_id)
            self.assertTrue(response["data"]["removed"])
            self.assertEqual(self.app.selected, [])
            self.assertIn(self.other, self.app.scene.root.children)
            self.assert_error(self.adapter.get_model_state(self.object_id), "model_not_found")
            self.ops.undo()
            items = self.adapter.list_models()["data"]["models"]
            restored = next(item for item in items if item["object_id"] != "model-2")
            self.assertNotEqual(restored["object_id"], self.object_id)
            self.assert_error(self.adapter.get_model_state(self.object_id), "model_not_found")
            with open(path, "rb") as stream:
                self.assertEqual(stream.read(), b"source remains")

    def test_noops_skip_push_and_do_not_invalidate_completed_slice(self):
        self.complete()
        for angle in (0, 360, -720):
            response = self.adapter.rotate_model(self.object_id, "x", angle)
            self.assertTrue(response["ok"], response)
            self.assertFalse(response["data"]["changed"])
        self.assertFalse(self.adapter.scale_model(self.object_id, {"x": 1, "y": 1, "z": 1})["data"]["changed"])
        self.assertFalse(self.adapter.move_model(self.object_id, {"x": 0, "y": 0, "z": 0})["data"]["changed"])
        self.assertEqual(self.ops.operations, [])
        self.assertTrue(self.adapter.get_slicing_status(self.slice_id)["data"]["valid"])
        self.assertTrue(self.adapter.rotate_model(self.object_id, "x", 30)["ok"])
        self.assert_error(self.adapter.get_slicing_result(self.slice_id), "slice_result_stale")

    def test_common_guard_and_native_busy_reject_mutations_but_reads_work(self):
        self.adapter._change_lock.acquire()
        try:
            for fn in (lambda: self.adapter.move_model(self.object_id, {"x": 1, "y": 2, "z": 3}),
                       lambda: self.adapter.rotate_model(self.object_id, "x", 1),
                       lambda: self.adapter.scale_model(self.object_id, {"x": 2, "y": 2, "z": 2}),
                       lambda: self.adapter.delete_model(self.object_id)):
                self.assert_error(fn(), "model_operation_busy")
            self.assertTrue(self.adapter.get_model_state(self.object_id)["ok"])
            self.assertTrue(self.adapter.list_models()["ok"])
        finally:
            self.adapter._change_lock.release()
        self.app.backend._slicing = True
        self.assert_error(self.adapter.delete_model(self.object_id), "model_operation_busy")
        self.app.backend._slicing = False
        self.device._writing = True
        self.assert_error(self.adapter.delete_model(self.object_id), "model_operation_busy")
        self.assertEqual(self.ops.operations, [])

    def test_cli_backend_without_basement_idle_busy_and_unknown_layout(self):
        from AIAssistant.slicer.slicing import backend_busy
        class CliParserBackend(object):
            def __init__(self):
                self._slicing = False
                self._start_slice_job = self._process_cli_job = self._process_layers_job = self._glicer_process = None
            def getPluginId(self):
                return "CLIParserBackend"
        backend = CliParserBackend()
        self.app.manager.current = self.app.manager.backends["cylindrical"] = backend
        self.app.stack.mode = "cylindrical"
        self.assertFalse(backend_busy(backend))
        self.assertTrue(self.adapter.move_model(self.object_id, {"x": 2, "y": 3, "z": 4})["ok"])
        for field in ("_start_slice_job", "_process_cli_job", "_process_layers_job"):
            setattr(backend, field, object())
            self.assertTrue(backend_busy(backend))
            self.assert_error(self.adapter.delete_model(self.object_id), "model_operation_busy")
            setattr(backend, field, None)
        backend._glicer_process = types.SimpleNamespace(poll=lambda: None)
        self.assertTrue(backend_busy(backend))
        self.assert_error(self.adapter.delete_model(self.object_id), "model_operation_busy")
        backend._glicer_process = types.SimpleNamespace(poll=lambda: 0)
        self.assertFalse(backend_busy(backend))
        class CylindricalBackend(CliParserBackend):
            def __init__(self):
                super().__init__()
                self._generate_basement_job = None
            def getPluginId(self):
                return "CylindricalBackend"
        full = CylindricalBackend()
        self.app.manager.current = self.app.manager.backends["cylindrical_full"] = full
        self.app.stack.mode = "cylindrical_full"
        self.assertFalse(backend_busy(full))
        self.assertTrue(self.adapter.move_model(self.object_id, {"x": 4, "y": 5, "z": 6})["ok"])
        for field in ("_generate_basement_job", "_process_cli_job", "_process_layers_job", "_start_slice_job"):
            setattr(full, field, object())
            self.assertTrue(backend_busy(full))
            self.assert_error(self.adapter.delete_model(self.object_id), "model_operation_busy")
            setattr(full, field, None)
        full._glicer_process = types.SimpleNamespace(poll=lambda: None)
        self.assertTrue(backend_busy(full))
        self.assert_error(self.adapter.delete_model(self.object_id), "model_operation_busy")
        unknown = types.SimpleNamespace(getPluginId=lambda: "UnknownBackend", _slicing=False, _start_slice_job=None)
        self.app.manager.current = unknown
        self.assert_error(self.adapter.delete_model(self.object_id), "backend_state_unavailable")
        unknown._generate_basement_job = unknown._process_layers_job = None
        self.assert_error(self.adapter.delete_model(self.object_id), "backend_state_unavailable")

    def test_push_ignored_or_adjusted_or_raises_is_not_success_and_no_rollback(self):
        self.ops.ignore = True
        self.assert_error(self.adapter.move_model(self.object_id, {"x": 1, "y": 2, "z": 3}), "model_transform_not_applied")
        self.assert_error(self.adapter.rotate_model(self.object_id, "x", 0.0001), "model_transform_not_applied")
        self.assert_error(self.adapter.delete_model(self.object_id), "model_transform_not_applied")
        self.ops.ignore = False
        self.ops.after_redo = lambda: self.node.setPosition(Vector(10, 0, 10))
        self.assert_error(self.adapter.move_model(self.object_id, {"x": 10, "y": 20, "z": 10}), "model_transform_not_applied")
        self.assertEqual(self.state()["position_mm"]["y"], 0)
        private_failure = "private path"
        def fail():
            raise RuntimeError(private_failure)
        self.ops.after_redo = fail
        with self.assertLogs("AIAssistant.slicer.adapter", level="ERROR") as captured:
            response = self.adapter.move_model(self.object_id, {"x": 2, "y": 2, "z": 2})
        self.assert_error(response, "slicer_error")
        self.assertNotIn("private path", response["error"]["message"] + "".join(captured.output))
        self.assertEqual(self.state()["position_mm"]["x"], 2)

    def test_parent_preview_inactive_disabled_singular_and_large_result_rejected_before_push(self):
        for setup, undo in ((lambda: setattr(self.app.scene.root, "matrix", [[2 * float(i == j) if i < 3 else float(i == j) for j in range(4)] for i in range(4)]),
                             lambda: setattr(self.app.scene.root, "matrix", model_tests.identity())),
                            (lambda: setattr(self.node, "enabled", False), lambda: setattr(self.node, "enabled", True)),
                            (lambda: setattr(self.node, "plate", 1), lambda: setattr(self.node, "plate", 0)),
                            (lambda: self.app.scene.root.add(Node(self.app, preview=True)), lambda: self.app.scene.root.children.pop())):
            setup()
            self.assert_error(self.adapter.rotate_model(self.object_id, "y", 90), "model_transform_unsupported")
            undo()
        self.node.matrix[0][0] = 0
        self.assert_error(self.adapter.scale_model(self.object_id, {"x": 2, "y": 2, "z": 2}), "model_transform_unsupported")
        self.node.matrix = model_tests.identity()
        self.assert_error(self.adapter.move_model(self.object_id, {"x": 1000000, "y": 0, "z": 0}), "model_transform_unsupported")
        self.node.matrix[0][0] = float("nan")
        self.assert_error(self.adapter.get_model_state(self.object_id), "model_transform_unsupported")
        self.assertEqual(self.ops.operations, [])

    def test_native_physics_enabled_or_unknown_rejected_no_preferences_write(self):
        for key in self.physics:
            for value in (True, None):
                self.physics[key] = value
                self.assert_error(self.adapter.move_model(self.object_id, {"x": 5, "y": 20, "z": 5}), "model_transform_unsupported")
                self.assertEqual(self.physics[key], value)
            self.physics[key] = False
        self.assertEqual(self.ops.operations, [])

    def test_outside_is_warning_not_printability_and_unknown_remains_null(self):
        response = self.adapter.move_model(self.object_id, {"x": 200, "y": 0, "z": 0})
        self.assertTrue(response["ok"], response)
        self.assertTrue(response["data"]["after"]["outside_build_area"])
        self.assertTrue(response["data"]["warnings"])
        self.volume.available = False
        self.assertIsNone(self.state()["outside_build_area"])

    def test_context_change_and_other_model_change_after_push_are_detected(self):
        self.ops.after_redo = lambda: setattr(self.app.plates, "activeBuildPlate", 1)
        self.assert_error(self.adapter.rotate_model(self.object_id, "y", 90), "model_transform_not_applied")
        self.app.plates.activeBuildPlate = 0
        self.ops.after_redo = lambda: self.other.setPosition(Vector(9, 9, 9))
        self.assert_error(self.adapter.rotate_model(self.object_id, "y", 90), "model_transform_not_applied")

    def test_pending_timeout_and_shutdown_never_execute_late_operations(self):
        worker = self.worker(lambda: self.adapter.delete_model(self.object_id))
        late = self.app.callbacks.get(timeout=1)
        self.assert_error(self.finish(worker), "qt_timeout")
        late()
        self.assertEqual(self.ops.operations, [])
        worker = self.worker(lambda: self.adapter.delete_model(self.object_id))
        late = self.app.callbacks.get(timeout=1)
        self.adapter.close()
        self.assert_error(self.finish(worker), "slicer_not_connected")
        late()
        self.assertEqual(self.ops.operations, [])

    def test_running_timeout_holds_common_guard_until_qt_returns(self):
        release, expired = threading.Event(), threading.Event()
        def after():
            self.assertTrue(release.wait(2))
        self.ops.after_redo = after
        worker = self.worker(lambda: self.adapter.move_model(self.object_id, {"x": 10, "y": 20, "z": 30}))
        callback = self.app.callbacks.get(timeout=1)
        def observe():
            worker[3].wait(2)
            if self.adapter._change_lock.locked():
                expired.set()
            release.set()
        observer = threading.Thread(target=observe)
        observer.start()
        callback()
        observer.join(2)
        self.assertTrue(expired.is_set())
        self.assert_error(self.finish(worker), "qt_timeout")
        self.assertFalse(self.adapter._change_lock.locked())
        self.assertEqual(self.state()["position_mm"]["x"], 10)

    def test_agent_new_mutation_real_registry_stops_after_error_no_later_delete(self):
        self.ops.ignore = True
        llm = agent_tests.ScriptedLLM(agent_tests.calls(
            agent_tests.tool("move_model", {"object_id": self.object_id, "position_mm": {"x": 1, "y": 2, "z": 3}}),
            agent_tests.tool("delete_model", {"object_id": self.object_id}, "delete")))
        service = AgentService(self.adapter, lambda: dict(agent_tests.CONFIG), llm, run_async=False)
        self.addCleanup(service.close)
        sid = service.create_session()["session_id"]
        service.submit_message(sid, {"text": "Перемести и удали", "client_message_id": "new"})
        results = [e["data"]["result"] for e in service.get_session(sid)["tool_events"] if e["type"] == "tool_finished"]
        self.assertEqual([r["error"]["code"] for r in results], ["model_transform_not_applied", "tool_not_executed"])
        self.assertEqual(len(llm.requests), 1)
        self.assertIn(self.node, self.app.scene.root.children)
        self.assertEqual(service.get_session(sid)["agent_state"], "idle")
