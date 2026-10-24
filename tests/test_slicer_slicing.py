"""TASK 05: fork-shaped signals/jobs and Qt queue; no real engine or Qt runtime."""
import json
import os
import queue
import sys
import tempfile
import threading
import types
import unittest
from enum import IntEnum
from unittest import mock
from urllib.request import Request, urlopen

import test_slicer_models as model_tests
import test_slicer_modes as mode_tests
import test_slicer_profiles as profile_tests
import test_slicer_settings as settings_tests
from AIAssistant.agent.mcp import dispatch
from AIAssistant.agent.service import AgentService
from AIAssistant.agent.tools import ToolRegistry
from AIAssistant.server import create_server
from AIAssistant.slicer.adapter import SlicerAdapter


class Signal(model_tests.Signal):
    def emit(self, *args):
        self.app.check_thread()
        for callback in list(self.slots):
            callback(*args)


class Matrix(list):
    def getData(self):
        return self


class Node(model_tests.Node):
    def getWorldTransformation(self):
        return Matrix(super().getWorldTransformation())


class State(IntEnum):
    NotStarted, Processing, Done, Error, Disabled = range(1, 6)


class Timer(object):
    app = None

    def __init__(self):
        self.app.check_thread()
        self.timeout = Signal(self.app)
        self.running, self.deleted = False, False

    def setInterval(self, interval):
        self.app.check_thread()
        self.interval = interval

    def start(self):
        self.app.check_thread()
        self.running = True

    def stop(self):
        self.app.check_thread()
        self.running = False

    def deleteLater(self):
        self.app.check_thread()
        self.deleted = True

    def fire(self):
        if self.running:
            self.timeout.emit()


class Stack(mode_tests.ModeStack):
    def __init__(self, app, name="global"):
        super().__init__(app)
        self.name = name
        self.extruders = {}
        self.propertyChanged, self.containersChanged = Signal(app), Signal(app)
        self.bottom = profile_tests.Container(app, "definition")
        self.quality = profile_tests.Container(app, "quality-normal", quality_type="normal")
        self.material = profile_tests.Container(app, "material-pla", {"material_diameter": 1.75})

    def getContainers(self):
        self.app.check_thread()
        return [self, self.quality, self.material, self.bottom]

    def getBottom(self):
        self.app.check_thread()
        return self.bottom

    def getAllKeys(self):
        self.app.check_thread()
        return {"layer_height", "printing_mode"}

    def getId(self):
        self.app.check_thread()
        return self.name

    def getMetaDataEntry(self, key):
        self.app.check_thread()
        return None

    def setProperty(self, key, prop, value):
        super().setProperty(key, prop, value)
        self.propertyChanged.emit(key, prop)


class Duration(object):
    def __init__(self, seconds):
        self.seconds = seconds

    def __int__(self):
        return self.seconds


class Information(object):
    def __init__(self, app):
        self.app = app
        self.currentPrintTimeChanged = Signal(app)
        self.materialLengthsChanged, self.materialWeightsChanged = Signal(app), Signal(app)
        self.seconds, self.lengths, self.weights = 99999, [999.0], [999.0]

    @property
    def currentPrintTime(self):
        self.app.check_thread()
        return Duration(self.seconds)

    @property
    def materialLengths(self):
        self.app.check_thread()
        return self.lengths

    @property
    def materialWeights(self):
        self.app.check_thread()
        return self.weights

    def update(self, seconds=3723, lengths=(1.23, 0.04), weights=(3.5, 0.1)):
        self.seconds, self.lengths, self.weights = seconds, list(lengths), list(weights)
        self.currentPrintTimeChanged.emit()
        self.materialLengthsChanged.emit()
        self.materialWeightsChanged.emit()


class Backend(mode_tests.Backend):
    def __init__(self, app):
        super().__init__(app, "CuraEngineBackend")
        for name in ("slicingStarted", "slicingCancelled", "backendStateChange", "processingProgress",
                     "backendError", "backendQuit", "printDurationMessage"):
            setattr(self, name, Signal(app))
        self._slicing = False
        self._start_slice_job = self._generate_basement_job = self._process_layers_job = None
        self._start_slice_job_build_plate = None
        self.calls, self.behavior = 0, "start"
        self.failure = "private engine value"

    def forceSlice(self):
        self.app.check_thread()
        self.calls += 1
        if self.behavior == "raise":
            raise RuntimeError(self.failure)
        if self.behavior == "no_start":
            self.processingProgress.emit(1.0)
            self.backendStateChange.emit(State.Done)
            return
        self._slicing = True
        if self.behavior != "queued":
            self.begin()

    def begin(self):
        # Как в fork: reset PI, начальный NotStarted, затем новый буфер и slicingStarted.
        self.app.info.update(0, (0.0,), (0.0,))
        self.backendStateChange.emit(State.NotStarted)
        self.processingProgress.emit(0)
        self.backendStateChange.emit(State.Processing)
        self.app.scene.gcode_dict[self.app.plates.activeBuildPlate] = []
        self.slicingStarted.emit()
        self._start_slice_job = object()
        self._start_slice_job_build_plate = self.app.plates.activeBuildPlate

    def estimates(self, plate=0, times=None, amounts=None, update=True):
        if self._start_slice_job is not None:
            self.prepare()
            # _onGCodeLayerMessage тоже заменяет пустой буфер. Даже без нового
            # progress этот буфер виден на собственном PrintTimeMaterialEstimates.
            self.app.scene.gcode_dict[0] = ["; engine data\nG1 X1"]
        self.printDurationMessage.emit(plate, {"infill": 3723.0} if times is None else times,
                                      [1234.0] if amounts is None else amounts)
        # UM.Signal functions (observer) precede methods (manager -> PI).
        if update:
            self.app.info.update()

    def prepare(self):
        self._start_slice_job = None
        self.app.scene.gcode_dict[0] = []
        self.backendStateChange.emit(State.Processing)

    def idle(self):
        self._slicing = False
        self._start_slice_job = self._generate_basement_job = self._process_layers_job = None
        self._start_slice_job_build_plate = None

    def finish(self):
        self.backendStateChange.emit(State.Done)
        self.processingProgress.emit(1.0)
        # Done ещё не означает финализированный G-code/слои/выход engine.
        self.app.scene.gcode_dict[0].append("; from real backend substitute\nG1 X1")
        self.idle()


class App(model_tests.ModelApp):
    def __init__(self):
        super().__init__()
        self.stack = Stack(self)
        self.stack.extruders = {"0": Stack(self, "extruder-0")}
        self.scene.root = Node(self, "root", mesh=False, sliceable=False)
        self.scene.sceneChanged = Signal(self)
        self.scene.gcode_dict = {0: ["old G-code"]}
        self.part = self.scene.root.add(Node(self))
        self.auto_slice = False
        self.info = Information(self)
        self.plates = types.SimpleNamespace(activeBuildPlate=0, activeBuildPlateChanged=Signal(self))
        self.globalContainerStackChanged = Signal(self)
        self.manager.currendBackendChanged = Signal(self)
        self.backend = Backend(self)
        for key in self.manager.backends:
            self.manager.backends[key] = Backend(self)
        self.manager.backends["classic"] = self.manager.current = self.backend
        self.manager.getBackends = self.getBackends

    def getBackends(self):
        self.check_thread()
        return self.manager.backends

    def callLater(self, callback, *args):
        super().callLater(lambda: callback(*args))

    def getPreferences(self):
        self.check_thread()
        return self

    def getValue(self, name):
        self.check_thread()
        if name != "general/auto_slice":
            raise AssertionError(name)
        return self.auto_slice

    def getPrintInformation(self):
        self.check_thread()
        return self.info

    def getMultiBuildPlateModel(self):
        self.check_thread()
        return self.plates


class SlicingTests(unittest.TestCase):
    worker = settings_tests.SettingsTests.worker
    finish = settings_tests.SettingsTests.finish
    pump_until_done = mode_tests.ModeTests.pump_until_done
    assert_error = mode_tests.ModeTests.assert_error

    def setUp(self):
        self.app = App()
        Timer.app = self.app
        qt = types.ModuleType("PyQt5.QtCore")
        qt.QTimer, qt.QUrl = Timer, model_tests.Url
        backend_module = types.ModuleType("UM.Backend.Backend")
        backend_module.BackendState = State
        self.patch = mock.patch.dict(sys.modules, {
            "PyQt5": types.ModuleType("PyQt5"), "PyQt5.QtCore": qt,
            "UM": types.ModuleType("UM"), "UM.Backend": types.ModuleType("UM.Backend"),
            "UM.Backend.Backend": backend_module})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.adapter = SlicerAdapter(self.app, timeout=0.25)
        self.addCleanup(lambda: self.adapter.close())
        self.tools = ToolRegistry(self.adapter)

    def drain(self):
        while not self.app.callbacks.empty():
            self.app.callbacks.get_nowait()()

    def start(self):
        response = self.adapter.start_slicing()
        self.assertTrue(response["ok"], response)
        self.slice_id = response["data"]["slice_id"]
        return response["data"]

    def status(self):
        return self.adapter.get_slicing_status(self.slice_id)["data"]

    def complete(self):
        self.start()
        self.app.backend.estimates()
        self.app.backend.finish()
        self.drain()
        self.assertEqual(self.status()["state"], "completed")
        return self.adapter.get_slicing_result(self.slice_id)["data"]

    def test_schemas_validation_and_dev_no_runtime(self):
        names = ("start_slicing", "get_slicing_status", "get_slicing_result")
        schemas = {t["name"]: t["inputSchema"] for t in self.tools.list_tools()}
        for name in names:
            schema = schemas[name]
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(schema["required"], [] if name == "start_slicing" else ["slice_id"])
            args = {} if name == "start_slicing" else {"slice_id": "slice-1"}
            response = dispatch(mode_tests.ModeTests.request(name, args), AgentService().tools)
            self.assertTrue(response["result"]["isError"])
            self.assertEqual(json.loads(response["result"]["content"][0]["text"])["error"]["code"], "slicer_not_connected")
            for invalid in ([], dict(args, extra=1), {"slice_id": ""}, {"slice_id": 1}, {"slice_id": None}):
                response = dispatch(mode_tests.ModeTests.request(name, invalid), self.tools)
                self.assertEqual(response["error"]["code"], -32602)
        self.assertEqual(self.app.backend.calls, 0)

    def test_preflight_rejections_never_launch_engine(self):
        checks = (
            (lambda: setattr(self.app, "stack", None), "no_active_machine"),
            (lambda: setattr(self.app.stack, "mode", "cylindrical"), "unsupported_slicing_mode"),
            (lambda: setattr(self.app.manager, "current", self.app.manager.backends["cylindrical"]), "unsupported_slicing_mode"),
            (lambda: setattr(self.app, "auto_slice", True), "auto_slicing_enabled"),
            (lambda: self.app.scene.root.children.clear(), "empty_scene"),
            (lambda: setattr(self.app.part, "plate", 1), "multiple_build_plates"),
            (lambda: self.app.scene.root.add(Node(self.app, preview=True)), "slicing_preview_active"),
            (lambda: setattr(self.app, "info", None), "slicing_failed"))
        for change, code in checks:
            with self.subTest(code=code):
                self.adapter.close()
                self.app = App()
                Timer.app = self.app
                self.adapter = SlicerAdapter(self.app)
                change()
                self.assert_error(self.adapter.start_slicing(), code)
                self.assertEqual(self.app.backend.calls, 0)
                self.assertFalse(self.adapter._change_lock.locked())

    def test_each_native_busy_job_blocks_start_and_lock_is_released(self):
        backend = self.app.backend
        for field in ("_slicing", "_start_slice_job", "_generate_basement_job", "_process_layers_job"):
            setattr(backend, field, True if field == "_slicing" else object())
            self.assert_error(self.adapter.start_slicing(), "slicing_busy")
            backend.idle()
        self.assertEqual(backend.calls, 0)
        self.start()

    def test_worker_fast_start_real_progress_and_delayed_completion(self):
        worker = self.worker(self.adapter.start_slicing)
        self.assertEqual(self.app.backend.calls, 0)
        self.app.callbacks.get(timeout=1)()
        start = self.finish(worker)
        self.assertTrue(start["ok"])
        self.slice_id = start["data"]["slice_id"]
        self.assertEqual(start["data"]["state"], "running")
        self.assertIsNone(start["data"]["progress"])
        self.assert_error(self.adapter.get_slicing_result(self.slice_id), "slice_not_ready")
        self.app.backend.processingProgress.emit(0.42)
        for bad in (float("nan"), float("inf"), -1, 2, True, "0.5"):
            self.app.backend.processingProgress.emit(bad)
        self.assertEqual(self.status()["progress"], 0.42)
        self.app.backend.backendStateChange.emit(State.Done)
        self.drain()
        self.assertEqual(self.status()["state"], "running")
        self.app.backend.estimates()
        self.app.scene.gcode_dict[0].append("G1 X1")
        self.app.backend.idle()
        self.drain()
        self.assertEqual(self.status()["state"], "completed")
        self.assertTrue(all(t is self.app.main for t in self.app.reads))

    def test_queued_then_start_and_done_requires_separate_qt_callback(self):
        self.app.backend.behavior = "queued"
        self.assertEqual(self.start()["state"], "queued")
        self.app.backend.begin()
        self.app.backend.estimates()
        self.drain()
        self.app.backend.finish()
        self.assertEqual(self.status()["state"], "running")
        self.drain()
        self.assertEqual(self.status()["state"], "completed")

    def test_print_information_units_totals_and_json_copy(self):
        result = self.complete()
        self.assertEqual(result["print_time_seconds"], 3723)
        self.assertAlmostEqual(result["material"]["length_mm"], 1270.0)
        self.assertEqual(result["material"]["weight_g"], 3.6)
        self.assertEqual(result["source"], "ste_slicer")
        self.assertEqual(result["backend_id"], "CuraEngineBackend")
        json.dumps(result, allow_nan=False)
        result["material"]["weight_g"] = -1
        self.assertEqual(self.adapter.get_slicing_result(self.slice_id)["data"]["material"]["weight_g"], 3.6)

    def test_fork_replaces_empty_buffers_in_preparation_and_first_engine_chunk(self):
        self.start()
        initial = self.app.scene.gcode_dict[0]
        self.app.backend.prepare()
        prepared = self.app.scene.gcode_dict[0]
        self.assertIsNot(initial, prepared)
        self.app.scene.gcode_dict[0] = ["; first engine chunk\nG1 X2"]
        self.app.backend.estimates()
        self.app.backend.finish()
        self.drain()
        self.assertEqual(self.status()["state"], "completed")

    def test_unknown_values_null_with_warnings_and_confirmed_zero_allowed(self):
        self.start()
        self.app.backend.estimates(times={"infill": float("nan")}, amounts=[float("nan")], update=False)
        self.app.info.update(0, (float("nan"),), ())
        self.app.backend.finish()
        self.drain()
        result = self.adapter.get_slicing_result(self.slice_id)["data"]
        self.assertIsNone(result["print_time_seconds"])
        self.assertEqual(result["material"], {"length_mm": None, "weight_g": None})
        self.assertEqual(len(result["warnings"]), 3)
        self.start()
        self.app.backend.estimates(times={"infill": 0}, amounts=[0], update=False)
        self.app.info.update(0, (0.0,), (0.0,))
        self.app.backend.finish()
        self.drain()
        result = self.adapter.get_slicing_result(self.slice_id)["data"]
        self.assertEqual(result["print_time_seconds"], 0)
        self.assertEqual(result["material"], {"length_mm": 0.0, "weight_g": 0.0})

    def test_progress_one_done_and_old_buffer_without_start_cannot_complete(self):
        self.app.backend.behavior = "no_start"
        self.start()
        self.drain()
        self.assertEqual(self.status()["state"], "failed")
        self.assert_error(self.adapter.get_slicing_result(self.slice_id), "slice_not_ready")
        self.assertFalse(self.adapter._change_lock.locked())

    def test_old_time_or_other_plate_estimate_or_missing_pi_update_is_not_result(self):
        self.start()
        self.app.backend.estimates(plate=1)
        self.app.backend.estimates(update=False)
        # PI callbacks above were fresh, so use new run to isolate no PI revision.
        self.app.backend.slicingCancelled.emit()
        self.app.backend.idle()
        self.status()
        self.start()
        self.app.backend.estimates(update=False)
        self.app.backend.finish()
        self.drain()
        self.assertEqual(self.status()["state"], "running")
        self.assert_error(self.adapter.get_slicing_result(self.slice_id), "slice_not_ready")

    def test_foreign_backend_and_job_or_plate_cannot_finish_our_run(self):
        foreign = Backend(self.app)
        self.start()
        foreign.backendStateChange.emit(State.Done)
        self.app.backend.backendError.emit(object())
        self.app.backend.estimates(plate=1)
        self.app.backend.finish()
        self.drain()
        self.assertEqual(self.status()["state"], "running")
        self.assertIsNone(self.adapter._slicing.record.statistics)

    def test_foreign_backend_cannot_overwrite_print_information_after_own_estimate(self):
        self.start()
        self.app.backend.estimates()
        other = self.app.manager.backends["cylindrical"]
        other.printDurationMessage.emit(0, {"infill": 999}, [999])
        self.app.info.update(999, (999,), (999,))
        self.app.backend.finish()
        self.drain()
        self.assertEqual(self.status()["error"]["code"], "slicing_conflict")
        self.assert_error(self.adapter.get_slicing_result(self.slice_id), "slice_not_ready")

    def test_replaced_gcode_buffer_and_conflicting_start_are_not_success(self):
        self.start()
        self.app.backend.estimates()
        self.app.backend.finish()
        self.app.scene.gcode_dict[0] = ["unrelated G-code"]
        self.drain()
        self.assertEqual(self.status()["state"], "failed")
        self.start()
        self.app.backend.slicingStarted.emit()
        self.assertEqual(self.status()["error"]["code"], "slicing_conflict")

    def test_cancellation_error_disabled_and_quit_remain_failed_on_late_done(self):
        events = (lambda b: b.slicingCancelled.emit(), lambda b: b.backendError.emit(b._start_slice_job),
                  lambda b: b.backendStateChange.emit(State.Error), lambda b: b.backendStateChange.emit(State.Disabled),
                  lambda b: b.backendQuit.emit(), lambda b: b.backendStateChange.emit(State.NotStarted))
        for event in events:
            self.start()
            event(self.app.backend)
            self.assertEqual(self.status()["state"], "failed")
            self.assertTrue(self.adapter._change_lock.locked())
            self.assert_error(self.adapter.start_slicing(), "slicing_busy")
            self.app.backend.estimates()
            self.app.backend.finish()
            self.drain()
            self.assertEqual(self.status()["state"], "failed")
            self.assertFalse(self.adapter._change_lock.locked())

    def test_timeout_is_finite_no_retry_and_guard_until_native_idle(self):
        self.start()
        deadline = self.adapter._slicing.record.deadline
        self.adapter._slicing.clock = lambda: deadline + 0.01
        self.adapter._slicing.timer.fire()
        self.assertEqual(self.status()["error"]["code"], "slicing_timeout")
        self.assertTrue(self.adapter._change_lock.locked())
        self.assert_error(self.adapter.set_setting("layer_height", "global", 0.24), "slicing_busy")
        self.assertEqual(self.app.backend.calls, 1)
        self.app.backend.estimates()
        self.app.backend.finish()
        self.drain()
        self.assertEqual(self.status()["state"], "failed")
        self.assertFalse(self.adapter._change_lock.locked())

    def test_all_writes_block_reads_available_then_setting_invalidates_result(self):
        self.start()
        self.assert_error(self.adapter.set_setting("layer_height", "global", 0.24), "slicing_busy")
        for callback, code in ((lambda: self.adapter.set_print_mode("classic"), "switch_in_progress"),
                               (lambda: self.adapter.select_profile("normal"), "selection_busy"),
                               (lambda: self.adapter.select_material("0", "pla"), "selection_busy")):
            self.assert_error(self.finish(self.worker(callback)), code)
        with tempfile.NamedTemporaryFile(suffix=".stl") as stream:
            stream.write(b"mesh")
            stream.flush()
            # Windows cannot reopen an open NamedTemporaryFile; only path checking is mocked.
            with mock.patch("AIAssistant.slicer.adapter.checked_model_path", return_value=stream.name):
                self.assert_error(self.finish(self.worker(lambda: self.adapter.load_model(stream.name))), "model_load_busy")
        self.assertTrue(self.adapter.get_setting("layer_height", "global")["ok"])
        self.assertEqual(len(self.adapter.list_models()["data"]["models"]), 1)
        self.app.backend.estimates()
        self.app.backend.finish()
        self.drain()
        self.assertTrue(self.adapter.set_setting("layer_height", "global", 0.24)["ok"])
        self.assertFalse(self.status()["valid"])
        self.assert_error(self.adapter.get_slicing_result(self.slice_id), "slice_result_stale")
        self.adapter.set_setting("layer_height", "global", 0.2)
        self.assertFalse(self.status()["valid"])

    def test_import_then_slice_and_no_start_during_unfinished_import(self):
        self.app.scene.root.children = []
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "small.stl")
            with open(path, "wb") as stream:
                stream.write(b"reader substitute input")
            worker = self.worker(lambda: self.adapter.load_model(path))
            self.app.callbacks.get(timeout=1)()
            self.assert_error(self.adapter.start_slicing(), "slicing_busy")
            self.assertEqual(self.app.backend.calls, 0)
            self.app.complete([Node(self.app)])
            imported = self.pump_until_done(worker)
            self.assertTrue(imported["ok"], imported)
            self.assertEqual(len(imported["data"]["objects"]), 1)
        self.assertEqual(self.complete()["source"], "ste_slicer")

    def test_start_exception_after_native_begin_keeps_guard_and_id_for_status(self):
        force = self.app.backend.forceSlice

        def fail_after_begin():
            force()
            raise RuntimeError(self.app.backend.failure)
        with mock.patch.object(self.app.backend, "forceSlice", side_effect=fail_after_begin):
            with self.assertLogs("AIAssistant.slicer.slicing", level="ERROR"):
                response = self.adapter.start_slicing()
        self.assert_error(response, "slicing_failed")
        self.slice_id = self.adapter._slicing.record.slice_id
        self.assertIn(self.slice_id, response["error"]["message"])
        self.assertEqual(self.status()["state"], "failed")
        self.assertTrue(self.adapter._change_lock.locked())
        self.assert_error(self.adapter.start_slicing(), "slicing_busy")
        self.app.backend.finish()
        self.drain()
        self.assertFalse(self.adapter._change_lock.locked())
        self.assertEqual(self.status()["state"], "failed")
        self.assert_error(self.adapter.get_slicing_result(self.slice_id), "slice_not_ready")

    def test_printable_context_changes_invalidate_but_camera_selection_preview_do_not(self):
        self.complete()
        service = self.app.scene.root.add(Node(self.app, "camera", sliceable=False))
        service.matrix[0][3] = 100
        self.app.scene.root.add(Node(self.app, "layers", sliceable=False))
        self.app.part.selected = True
        self.app.scene.sceneChanged.emit(service)
        self.assertTrue(self.status()["valid"])
        self.app.part.matrix[0][3] = 1
        self.app.scene.sceneChanged.emit(self.app.part)
        self.assert_error(self.adapter.get_slicing_result(self.slice_id), "slice_result_stale")

    def test_settings_material_quality_machine_plate_and_mesh_revision(self):
        changes = (lambda: setattr(self.app.stack, "value", 0.24),
                   lambda: self.app.stack.material.values.update(material_diameter=2.85),
                   lambda: setattr(self.app.stack.quality, "container_id", "quality-fine"),
                   lambda: setattr(self.app, "stack", Stack(self.app)),
                   lambda: setattr(self.app.plates, "activeBuildPlate", 1),
                   lambda: setattr(self.app.part, "mesh", model_tests.Mesh(self.app)))
        for change in changes:
            self.app.plates.activeBuildPlate = 0
            self.complete()
            change()
            self.assert_error(self.adapter.get_slicing_result(self.slice_id), "slice_result_stale")

    def test_ui_edit_during_running_and_manual_new_job_fail(self):
        self.start()
        self.app.part.matrix[2][3] = 10
        self.app.scene.sceneChanged.emit(self.app.part)
        self.assertEqual(self.status()["error"]["code"], "slicing_context_changed")
        self.app.backend.idle()
        self.status()
        self.start()
        self.app.backend._start_slice_job = object()
        self.assertEqual(self.status()["error"]["code"], "slicing_conflict")

    def test_ids_last_completed_retained_stale_and_old_timer_detached(self):
        self.complete()
        old_id, old_timer = self.slice_id, self.adapter._slicing.timer
        self.start()
        self.assertNotEqual(old_id, self.slice_id)
        self.assertTrue(old_timer.deleted)
        self.assertEqual(old_timer.timeout.slots, [])
        self.assert_error(self.adapter.get_slicing_result(old_id), "slice_result_stale")
        self.assert_error(self.adapter.get_slicing_status("unknown"), "slice_not_found")
        self.assert_error(self.adapter.get_slicing_result("unknown"), "slice_not_found")

    def test_shutdown_disconnects_timer_signals_pending_callbacks_no_engine_stop(self):
        self.start()
        self.app.backend.estimates()
        timer = self.adapter._slicing.timer
        self.adapter.close()
        self.drain()
        self.adapter.close()
        self.assertTrue(timer.deleted)
        self.assertFalse(timer.running)
        for name in ("slicingStarted", "slicingCancelled", "backendStateChange", "processingProgress", "printDurationMessage"):
            self.assertEqual(getattr(self.app.backend, name).slots, [])
        self.assertEqual(self.app.info.currentPrintTimeChanged.slots, [])
        self.assertEqual(self.app.scene.sceneChanged.slots, [])
        self.assertFalse(self.adapter._change_lock.locked())
        self.assertTrue(self.app.backend._slicing)
        self.assert_error(self.adapter.start_slicing(), "slicer_not_connected")

    def test_pending_start_timeout_cancels_late_launch(self):
        self.adapter._bridge._timeout = 0.01
        worker = self.worker(self.adapter.start_slicing)
        callback = self.app.callbacks.get(timeout=1)
        self.assert_error(self.finish(worker), "qt_timeout")
        callback()
        self.assertEqual(self.app.backend.calls, 0)

    def test_worker_signal_is_deferred_before_api_and_shutdown_ignores_it(self):
        self.start()
        callback = self.app.backend.processingProgress.slots[0]
        self.finish(self.worker(lambda: callback(0.51)))
        self.assertIsNone(self.adapter._slicing.record.progress)
        self.drain()
        self.assertEqual(self.status()["progress"], 0.51)
        self.finish(self.worker(lambda: callback(0.8)))
        self.adapter.close()
        self.drain()

    def test_api_errors_not_success_or_private_exception_leak(self):
        self.app.backend.behavior = "raise"
        with self.assertLogs("AIAssistant.slicer.slicing", level="ERROR") as captured:
            self.assert_error(self.adapter.start_slicing(), "slicing_failed")
        self.assertNotIn("private engine value", "".join(captured.output))
        self.assertFalse(self.adapter._change_lock.locked())
        self.app.backend.behavior = "start"
        self.start()
        with mock.patch.object(self.app, "getPrintInformation", side_effect=RuntimeError("private stats")):
            self.app.backend.estimates()
            with self.assertLogs("AIAssistant.slicer.slicing", level="ERROR"):
                self.drain()
        self.assertEqual(self.status()["state"], "failed")

    def test_http_mcp_start_status_result_operational_envelope(self):
        service = AgentService(self.adapter)
        server = create_server(service=service)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:%d/mcp" % server.server_address[1]

        def request(name, arguments):
            body = json.dumps(mode_tests.ModeTests.request(name, arguments)).encode("utf-8")
            with urlopen(Request(url, data=body, headers={"Content-Type": "application/json"}), timeout=2) as response:
                return json.load(response)
        response = self.pump_until_done(self.worker(lambda: request("start_slicing", {})))
        self.slice_id = json.loads(response["result"]["content"][0]["text"])["data"]["slice_id"]
        response = self.pump_until_done(self.worker(lambda: request("get_slicing_status", {"slice_id": self.slice_id})))
        self.assertEqual(json.loads(response["result"]["content"][0]["text"])["data"]["state"], "running")
        response = self.pump_until_done(self.worker(lambda: request("get_slicing_result", {"slice_id": self.slice_id})))
        self.assertTrue(response["result"]["isError"])
        self.app.backend.estimates()
        self.app.backend.finish()
        self.drain()
        response = self.pump_until_done(self.worker(lambda: request("get_slicing_result", {"slice_id": self.slice_id})))
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(json.loads(response["result"]["content"][0]["text"])["data"]["source"], "ste_slicer")
