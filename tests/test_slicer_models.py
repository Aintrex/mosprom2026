"""TASK 04: fake scene/readLocalFile, real local file checks and HTTP/MCP."""
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
from urllib.request import Request, urlopen

import test_slicer_modes as modes_tests
import test_slicer_settings as settings_tests
from AIAssistant.agent.mcp import dispatch
from AIAssistant.agent.service import AgentService
from AIAssistant.agent.tools import ToolRegistry
from AIAssistant.server import create_server
from AIAssistant.slicer.adapter import SlicerAdapter
from AIAssistant.slicer import models


class Signal(object):
    def __init__(self, app):
        self.app, self.slots = app, []

    def connect(self, callback):
        self.app.check_thread()
        self.slots.append(callback)

    def disconnect(self, callback):
        self.app.check_thread()
        self.slots.remove(callback)

    def emit(self, path):
        self.app.check_thread()
        for callback in list(self.slots):
            callback(path)


def identity():
    return [[float(i == j) for j in range(4)] for i in range(4)]


class Vector(object):
    def __init__(self, values):
        self.x, self.y, self.z = values


class Box(object):
    def __init__(self, lo, hi):
        self.minimum, self.maximum = Vector(lo), Vector(hi)


class Mesh(object):
    def __init__(self, app, points=None):
        self.app = app
        self.points = points if points is not None else [(0, 0, 0), (2, 3, 4)]
        self.unavailable = False

    def getVertexCount(self):
        self.app.check_thread()
        return len(self.points)

    def getExtents(self, matrix):
        self.app.check_thread()
        if self.unavailable:
            return None
        if matrix is None:
            raise AssertionError("Must pass world transformation")
        transformed = [[sum(matrix[i][j] * (list(p) + [1])[j] for j in range(4)) for i in range(3)] for p in self.points]
        return Box([min(p[i] for p in transformed) for i in range(3)],
                   [max(p[i] for p in transformed) for i in range(3)])


class Node(object):
    def __init__(self, app, name="part.stl", mesh=True, group=False, sliceable=True, preview=False):
        self.app, self.name = app, name
        self.mesh = Mesh(app) if mesh else None
        self.group, self.sliceable, self.preview = group, sliceable, preview
        self.children, self.parent = [], None
        self.matrix = identity()
        self.plate = 0

    def add(self, child):
        self.app.check_thread()
        self.children.append(child)
        child.parent = self
        return child

    def getChildren(self):
        self.app.check_thread()
        return list(self.children)

    def getMeshData(self):
        self.app.check_thread()
        return self.mesh

    def callDecoration(self, name):
        self.app.check_thread()
        return {"isGroup": self.group, "isSliceable": self.sliceable,
                "isBlockSlicing": self.preview, "getBuildPlateNumber": self.plate}.get(name)

    def getName(self):
        self.app.check_thread()
        return self.name

    def getWorldTransformation(self):
        self.app.check_thread()
        if self.parent is None:
            return self.matrix
        parent = self.parent.getWorldTransformation()
        return [[sum(parent[i][k] * self.matrix[k][j] for k in range(4)) for j in range(4)] for i in range(4)]

    def getBoundingBox(self):
        raise AssertionError("Parent bbox contains artificial origin/service geometry")


class Scene(object):
    def __init__(self, app):
        self.app = app
        self.root = Node(app, "root", mesh=False, sliceable=False)

    def getRoot(self):
        self.app.check_thread()
        return self.root


class Controller(object):
    def __init__(self, app):
        self.app = app

    def getScene(self):
        self.app.check_thread()
        return self.app.scene


class Url(object):
    def __init__(self, path):
        self.path = path

    @staticmethod
    def fromLocalFile(path):
        return Url(path)

    def toLocalFile(self):
        return self.path


class ModelApp(modes_tests.ModeApp):
    def __init__(self):
        super().__init__()
        self.scene = Scene(self)
        self.controller = Controller(self)
        self.fileLoaded, self.fileCompleted = Signal(self), Signal(self)
        self.loads = []
        self.load_error = None

    def getController(self):
        self.check_thread()
        return self.controller

    def readLocalFile(self, url, skip_project_file_check=False):
        self.check_thread()
        self.loads.append((url.toLocalFile(), skip_project_file_check))
        if self.load_error:
            raise self.load_error

    def complete(self, nodes, path=None):
        path = path or self.loads[-1][0]
        self.fileLoaded.emit(path)
        for node in nodes:
            self.scene.root.add(node)
        self.fileCompleted.emit(path)


class ModelTests(unittest.TestCase):
    worker = settings_tests.SettingsTests.worker
    finish = settings_tests.SettingsTests.finish
    pump_until_done = modes_tests.ModeTests.pump_until_done
    assert_error = modes_tests.ModeTests.assert_error

    def setUp(self):
        self.app = ModelApp()
        self.adapter = SlicerAdapter(self.app, timeout=0.5, load_timeout=0.25)
        self.tools = ToolRegistry(self.adapter)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = self.file("part.STL", b"mesh input")
        qt = types.ModuleType("PyQt5.QtCore")
        qt.QUrl = Url
        self.qt_patch = mock.patch.dict(sys.modules, {"PyQt5": types.ModuleType("PyQt5"), "PyQt5.QtCore": qt})
        self.qt_patch.start()
        self.addCleanup(self.qt_patch.stop)
        self.addCleanup(self.adapter.close)

    def file(self, name, content):
        path = os.path.join(self.directory.name, name)
        with open(path, "wb") as stream:
            stream.write(content)
        return path

    def start(self, path=None):
        worker = self.worker(lambda: self.adapter.load_model(path or self.path))
        self.app.callbacks.get(timeout=1)()
        return worker

    def test_argument_validation_before_api_or_file_io(self):
        cases = (("load_model", {}), ("load_model", {"path": ""}), ("load_model", {"path": 123}),
                 ("load_model", {"path": self.path, "extra": 1}), ("list_models", {"extra": 1}),
                 ("get_model_bounds", {}), ("get_model_bounds", {"object_id": False}),
                 ("get_model_bounds", {"object_id": ""}))
        with mock.patch.object(models, "checked_model_path", side_effect=AssertionError("No filesystem validation")):
            for name, arguments in cases:
                response = dispatch(modes_tests.ModeTests.request(name, arguments), self.tools)
                self.assertEqual(response["error"]["code"], -32602)
        self.assertEqual(self.app.loads, [])
        self.assertEqual(self.app.reads, [])

    def test_file_constraints_fail_without_starting_job(self):
        empty = self.file("empty.stl", b"")
        big = self.file("big.obj", b"x")
        with open(big, "r+b") as stream:
            stream.truncate(models.MAX_MODEL_BYTES + 1)
        directory_stl = os.path.join(self.directory.name, "directory.stl")
        os.mkdir(directory_stl)
        for path, code in (("relative.stl", "invalid_model_path"), ("https://example.org/model.stl", "invalid_model_path"),
                           ("file:///C:/model.stl", "invalid_model_path"), ("\\\\server\\share\\m.stl", "invalid_model_path"),
                           ("//server/share/m.stl", "invalid_model_path"), (self.path + "\x00", "invalid_model_path"),
                           (self.file("wrong.txt", b"x"), "unsupported_model_file"),
                           (os.path.join(self.directory.name, "absent.stl"), "model_file_unavailable"),
                           (directory_stl, "invalid_model_file"), (empty, "invalid_model_file"), (big, "invalid_model_file")):
            result = self.finish(self.worker(lambda: self.adapter.load_model(path)))
            self.assert_error(result, code)
            self.assertNotIn(self.directory.name, result["error"]["message"])
        self.assertEqual(self.app.loads, [])

    def test_unreadable_file_and_filesystem_checks_stay_outside_qt(self):
        original = models.checked_model_path

        def check(path):
            self.assertIsNot(threading.current_thread(), self.app.main)
            return original(path)
        with mock.patch.object(models, "checked_model_path", side_effect=check):
            worker = self.start()
        self.app.complete([Node(self.app)])
        self.assertTrue(self.pump_until_done(worker)["ok"])
        with mock.patch("AIAssistant.slicer.models.open", side_effect=PermissionError("private path"), create=True):
            self.assert_error(self.finish(self.worker(lambda: self.adapter.load_model(self.path))), "model_file_unavailable")

    def test_empty_scene_and_filtering_services_groups_and_preview(self):
        self.assertEqual(self.adapter.list_models(), {"ok": True, "data": {"models": []}})
        self.app.scene.root.add(Node(self.app, "camera", sliceable=False))
        self.app.scene.root.add(Node(self.app, "platform", sliceable=False))
        self.app.scene.root.add(Node(self.app, "preview", preview=True))
        group = self.app.scene.root.add(Node(self.app, "group", mesh=False, group=True))
        group.add(Node(self.app, "part1"))
        group.add(Node(self.app, "part2"))
        group.add(Node(self.app, "service", sliceable=False))
        items = self.adapter.list_models()["data"]["models"]
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["is_group"])

    def test_same_names_stable_ids_and_stale_ids_never_reused(self):
        first = self.app.scene.root.add(Node(self.app, "same.stl"))
        self.app.scene.root.add(Node(self.app, "same.stl"))
        items = self.adapter.list_models()["data"]["models"]
        ids = [item["object_id"] for item in items]
        self.assertNotEqual(*ids)
        self.assertEqual(ids, [item["object_id"] for item in self.adapter.list_models()["data"]["models"]])
        self.app.scene.root.children.remove(first)
        self.assert_error(self.adapter.get_model_bounds(ids[0]), "model_not_found")
        self.app.scene.root.add(first)
        self.assertNotIn(ids[0], [item["object_id"] for item in self.adapter.list_models()["data"]["models"]])
        self.app.scene = Scene(self.app)
        self.assert_error(self.adapter.get_model_bounds(ids[1]), "model_not_found")

    def test_world_bounds_group_does_not_include_empty_parent_origin(self):
        group = self.app.scene.root.add(Node(self.app, "group", mesh=False, group=True))
        group.matrix[0][3], group.matrix[1][3], group.matrix[2][3] = 100, 10, 50
        child = group.add(Node(self.app))
        child.matrix[0][0], child.matrix[2][2] = 2, 3
        child.matrix[0][3], child.matrix[1][3], child.matrix[2][3] = 5, 2, 7
        child.mesh.points = [(1, 1, 1), (3, 4, 2)]
        result = self.adapter.list_models()["data"]["models"][0]
        self.assertEqual(result["bounds"], {"coordinate_system": "uranium_world_y_up", "unit": "mm",
            "min": {"x": 107.0, "y": 13.0, "z": 60.0}, "max": {"x": 111.0, "y": 16.0, "z": 63.0},
            "size": {"x": 4.0, "y": 3.0, "z": 3.0}})
        current = self.adapter.get_model_bounds(result["object_id"])
        self.assertEqual(current["data"]["bounds"], result["bounds"])
        child.matrix[1][3] += 5
        self.assertEqual(self.adapter.get_model_bounds(result["object_id"])["data"]["bounds"]["min"]["y"], 18.0)

    def test_rotation_flat_model_missing_and_nonfinite_bounds(self):
        node = self.app.scene.root.add(Node(self.app))
        node.mesh.points = [(0, 0, 0), (3, 0, 2)]
        node.matrix[0][0], node.matrix[0][2], node.matrix[2][0], node.matrix[2][2] = 0, 1, -1, 0
        item = self.adapter.list_models()["data"]["models"][0]
        self.assertEqual(item["bounds"]["size"], {"x": 2.0, "y": 0.0, "z": 3.0})
        for unavailable in (True, False):
            node.mesh.unavailable = unavailable
            if not unavailable:
                node.matrix[0][3] = float("nan")
            result = self.adapter.list_models()
            self.assertIsNone(result["data"]["models"][0]["bounds"])
            json.dumps(result, allow_nan=False)
            self.assert_error(self.adapter.get_model_bounds(item["object_id"]), "model_bounds_unavailable")

    def test_read_and_signals_use_qt_thread(self):
        worker = self.worker(self.adapter.list_models)
        callback = self.app.callbacks.get(timeout=1)
        self.assertEqual(self.app.reads, [])
        callback()
        self.assertTrue(self.finish(worker)["ok"])
        worker = self.start()
        self.app.complete([Node(self.app)])
        self.assertTrue(self.pump_until_done(worker)["ok"])
        self.assertTrue(all(t is self.app.main for t in self.app.reads))

    def test_early_loaded_and_stray_node_or_foreign_signal_do_not_confirm(self):
        worker = self.start()
        path = self.app.loads[-1][0]
        self.app.fileLoaded.emit(path + ".foreign")
        self.app.fileCompleted.emit(path + ".foreign")
        self.app.fileLoaded.emit(path)
        self.app.scene.root.add(Node(self.app))
        self.app.callbacks.get(timeout=1)()
        self.assertFalse(worker[3].is_set())
        self.app.fileCompleted.emit(path)
        self.assertTrue(self.pump_until_done(worker)["ok"])
        self.assertEqual(self.app.fileLoaded.slots, [])
        self.assertEqual(self.app.fileCompleted.slots, [])

    def test_own_completion_empty_or_without_loaded_is_failure(self):
        for emit_loaded in (True, False):
            worker = self.start()
            if emit_loaded:
                self.app.fileLoaded.emit(self.app.loads[-1][0])
            self.app.fileCompleted.emit(self.app.loads[-1][0])
            self.assert_error(self.pump_until_done(worker), "model_load_failed")
            self.assertEqual(self.app.fileCompleted.slots, [])

    def test_stl_obj_3mf_mesh_import_multiple_objects_and_repeat(self):
        old = self.app.scene.root.add(Node(self.app, "existing"))
        known = self.adapter.list_models()["data"]["models"][0]["object_id"]
        emitted_ids = set()
        for filename in ("part.STL", "part.obj", "project.3mf", "project.3mf"):
            path = self.file(filename, b"reader fake input")
            worker = self.start(path)
            group = Node(self.app, path, mesh=False, group=True)
            group.add(Node(self.app))
            self.app.complete([group, Node(self.app, path)])
            result = self.pump_until_done(worker)
            self.assertTrue(result["ok"])
            self.assertEqual(result["data"]["file_name"], filename)
            self.assertEqual(len(result["data"]["objects"]), 2)
            ids = {item["object_id"] for item in result["data"]["objects"]}
            self.assertFalse(ids & emitted_ids)
            emitted_ids.update(ids)
            self.assertNotIn(self.directory.name, json.dumps(result))
            self.assertTrue(self.app.loads[-1][1])
        self.assertIn(old, self.app.scene.root.children)
        self.assertTrue(self.adapter.get_model_bounds(known)["ok"])

    def test_no_machine_and_preview_rejected_before_loading(self):
        stack = self.app.stack
        self.app.stack = None
        self.assert_error(self.finish(self.start()), "no_active_machine")
        self.app.stack = stack
        self.app.scene.root.add(Node(self.app, "gcode", preview=True))
        self.assert_error(self.finish(self.start()), "model_preview_active")
        self.assertEqual(self.app.loads, [])
        self.assertEqual(self.app.fileLoaded.slots, [])

    def test_detected_scene_mutations_are_not_attributed_to_load(self):
        worker = self.start()
        self.app.scene.root.add(Node(self.app, "unrelated"))
        self.app.complete([Node(self.app, "own")])
        self.assert_error(self.pump_until_done(worker), "model_scene_changed")
        worker = self.start()
        self.app.stack = modes_tests.ModeStack(self.app)
        self.app.complete([Node(self.app)])
        self.assert_error(self.pump_until_done(worker), "model_scene_changed")
        worker = self.start()
        self.app.scene = Scene(self.app)
        self.app.complete([Node(self.app)])
        self.assert_error(self.pump_until_done(worker), "model_scene_changed")

    def test_timeout_before_start_has_no_late_import(self):
        worker = self.worker(lambda: self.adapter.load_model(self.path))
        callback = self.app.callbacks.get(timeout=1)
        self.assert_error(self.finish(worker), "qt_timeout")
        callback()
        self.assertEqual(self.app.loads, [])
        self.assertFalse(self.adapter._change_lock.locked())

    def test_expired_preparation_is_cancelled_before_readlocalfile(self):
        worker = self.worker(lambda: self.adapter.load_model(self.path))
        callback = self.app.callbacks.get(timeout=1)
        original = self.app.getGlobalContainerStack

        def delayed_machine():
            self.assertTrue(worker[3].wait(2))
            return original()
        with mock.patch.object(self.app, "getGlobalContainerStack", side_effect=delayed_machine):
            callback()
        self.assert_error(self.finish(worker), "qt_timeout")
        self.assertEqual(self.app.loads, [])
        self.assertFalse(self.adapter._change_lock.locked())
        self.assertEqual(self.app.fileLoaded.slots, [])

    def test_loading_uses_one_budget_including_initial_queue_delay(self):
        clock, budgets = [0.0], []
        fake_time = types.SimpleNamespace(monotonic=lambda: clock[0])
        original_run, original_check = self.adapter._bridge.run, models.ModelLoad.check
        def run(operation, timeout=None):
            budgets.append(timeout)
            return original_run(operation, timeout=timeout)
        def check(operation, app):
            result = original_check(operation, app)
            clock[0] = self.adapter._load_timeout  # Deadline after a completed Qt check, not a queue race.
            return result
        with mock.patch("AIAssistant.slicer.adapter.time", fake_time), \
                mock.patch("AIAssistant.slicer.qt_bridge.time", fake_time), \
                mock.patch.object(self.adapter._bridge, "run", side_effect=run), \
                mock.patch.object(models.ModelLoad, "check", check):
            worker = self.worker(lambda: self.adapter.load_model(self.path))
            callback = self.app.callbacks.get(timeout=1)
            clock[0] = 0.15  # Simulate time spent in the initial Qt queue.
            callback()
            self.app.callbacks.get(timeout=1)()
            self.assert_error(self.finish(worker), "model_load_timeout")
        self.assertAlmostEqual(budgets[0], self.adapter._load_timeout)
        self.assertAlmostEqual(budgets[1], self.adapter._load_timeout - 0.15)
        self.assertEqual(len(budgets), 2)  # No fresh budget after queue delay.
        self.app.complete([])

    def test_timeout_after_start_retains_job_lock_then_late_completion_cleans_up(self):
        worker = self.start()
        result = self.pump_until_done(worker)
        self.assert_error(result, "model_load_timeout")
        self.assertIn("list_models", result["error"]["message"])
        self.assert_error(self.finish(self.worker(lambda: self.adapter.load_model(self.path))), "model_load_busy")
        self.assert_error(self.finish(self.worker(lambda: self.adapter.set_print_mode("classic"))), "switch_in_progress")
        self.assert_error(self.finish(self.worker(lambda: self.adapter.select_profile("id"))), "selection_busy")
        self.assert_error(self.finish(self.worker(lambda: self.adapter.select_material("0", "id"))), "selection_busy")
        self.assertTrue(self.adapter.list_models()["ok"])
        self.app.complete([Node(self.app)])
        self.assertEqual(len(self.app.loads), 1)
        self.assertFalse(self.adapter._change_lock.locked())
        self.assertEqual(self.app.fileLoaded.slots, [])
        self.assertEqual(len(self.adapter.list_models()["data"]["models"]), 1)
        worker = self.start()
        self.app.complete([Node(self.app)])
        self.assertTrue(self.pump_until_done(worker)["ok"])

    def test_api_exception_is_error_without_private_path_and_retains_uncertain_job(self):
        self.app.load_error = KeyError(self.path)
        worker = self.worker(lambda: self.adapter.load_model(self.path))
        with self.assertLogs("AIAssistant.slicer.adapter", level="ERROR") as captured:
            self.app.callbacks.get(timeout=1)()
            result = self.finish(worker)
        self.assert_error(result, "slicer_error")
        self.assertNotIn(self.path, result["error"]["message"])
        self.assertNotIn(self.path, "".join(captured.output))
        self.assertTrue(self.adapter._change_lock.locked())
        self.app.complete([])
        self.assertFalse(self.adapter._change_lock.locked())

    def test_shutdown_disconnects_on_qt_and_pending_start_is_cancelled(self):
        worker = self.start()
        self.adapter.close()
        self.assert_error(self.finish(worker), "slicer_not_connected")
        self.assertEqual(self.app.fileLoaded.slots, [])
        self.assertEqual(self.app.fileCompleted.slots, [])
        self.assertFalse(self.adapter._change_lock.locked())
        self.adapter = SlicerAdapter(self.app, load_timeout=0.2)
        worker = self.worker(lambda: self.adapter.load_model(self.path))
        callback = self.app.callbacks.get(timeout=1)
        self.adapter.close()
        self.assert_error(self.finish(worker), "slicer_not_connected")
        callback()
        self.assertEqual(len(self.app.loads), 1)

    def test_direct_main_thread_load_rejected_without_file_read_or_start(self):
        with mock.patch.object(models, "checked_model_path", side_effect=AssertionError("No file read on Qt")):
            self.assert_error(self.adapter.load_model(self.path), "slicer_error")
        self.assertEqual(self.app.loads, [])

    def test_mcp_schemas_and_dev_without_app(self):
        tools = {tool["name"]: tool for tool in self.tools.list_tools()}
        for name, arguments in (("list_models", {}), ("load_model", {"path": self.path}), ("get_model_bounds", {"object_id": "opaque"})):
            schema = tools[name]["inputSchema"]
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(set(schema["required"]), set(arguments))
            response = dispatch(modes_tests.ModeTests.request(name, arguments), AgentService().tools)
            self.assertTrue(response["result"]["isError"])
            self.assert_error(json.loads(response["result"]["content"][0]["text"]), "slicer_not_connected")

    def test_http_load_and_bounds_unknown_id_is_operational_error(self):
        server = create_server(AgentService(self.adapter))
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        try:
            def post(name, arguments):
                request = Request("http://127.0.0.1:%d/mcp" % server.server_port,
                    data=json.dumps(modes_tests.ModeTests.request(name, arguments)).encode("utf-8"),
                    headers={"Content-Type": "application/json"})
                with urlopen(request, timeout=3) as response:
                    return json.loads(response.read().decode("utf-8"))
            worker = self.worker(lambda: post("load_model", {"path": self.path}))
            self.app.callbacks.get(timeout=1)()
            self.app.complete([Node(self.app)])
            response = self.pump_until_done(worker)
            self.assertFalse(response["result"]["isError"])
            worker = self.worker(lambda: post("get_model_bounds", {"object_id": "unknown"}))
            self.app.callbacks.get(timeout=1)()
            response = self.finish(worker)
            self.assertTrue(response["result"]["isError"])
            self.assert_error(json.loads(response["result"]["content"][0]["text"]), "model_not_found")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == "__main__":
    unittest.main()
